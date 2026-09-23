package pii

import (
	"context"
	"strings"
	"sync"
	"testing"
	"time"
)

// All-zero fixtures: zero check digits are checksum-correct for all-zero
// bodies (Luhn and INN alike), so no test needs anything resembling a real
// card number, INN, passport, phone number, or email address.
var (
	dummyCard       = strings.Repeat("0", 16)
	dummyCardSpaced = "0000 0000 0000 0000"
	dummyPassport   = "0000 000000"
	dummyPhone      = "+7 900 000-00-00"
	dummyInn12      = strings.Repeat("0", 12)
)

func TestLuhn(t *testing.T) {
	if !LuhnValid(dummyCard) {
		t.Fatal("zero-checksum dummy rejected")
	}
	if LuhnValid(dummyCard[:15] + "1") {
		t.Fatal("invalid card accepted")
	}
}

func TestInnChecksum(t *testing.T) {
	if !InnValid(dummyInn12) {
		t.Fatal("valid 12-digit INN rejected")
	}
	if !InnValid(strings.Repeat("0", 10)) {
		t.Fatal("valid 10-digit INN rejected")
	}
	if InnValid(dummyInn12+"0") || InnValid(strings.Repeat("0", 9)+"1") {
		t.Fatal("invalid INN accepted")
	}
}

func TestDetectStructured(t *testing.T) {
	text := "Клиент: email client@example.com, телефон " + dummyPhone + ", ИНН " + dummyInn12 + ", карта " + dummyCardSpaced + ", паспорт " + dummyPassport + "."
	cands := Detect(text)
	types := map[PIIType]bool{}
	for _, c := range cands {
		types[c.Type] = true
	}
	for _, want := range []PIIType{TEmail, TPhone, TInn, TCardNumber, TPassport} {
		if !types[want] {
			t.Fatalf("missing %s in %+v", want, cands)
		}
	}
}

func TestPinRequiresCard(t *testing.T) {
	alone := Decide("Введите пин-код 0000 в приложении.", Detect("Введите пин-код 0000 в приложении."))
	for _, d := range alone {
		if d.Type == TPin && d.Decision == Mask {
			t.Fatal("PIN without card must be KEEP")
		}
	}
	withCard := "Карта " + dummyCardSpaced + ", пин-код 0000."
	ds := Decide(withCard, Detect(withCard))
	foundMaskedPin := false
	for _, d := range ds {
		if d.Type == TPin && d.Decision == Mask {
			foundMaskedPin = true
		}
	}
	if !foundMaskedPin {
		t.Fatal("PIN next to card must be MASK")
	}
}

func TestPublicVsPrivateName(t *testing.T) {
	pub := "Александр Пушкин написал роман в стихах."
	ds := Decide(pub, Detect(pub))
	for _, d := range ds {
		if d.Type == TFullName && d.Decision == Mask {
			t.Fatalf("public poet masked: %+v", d)
		}
	}
	priv := "Клиент Пушкин Александр Сергеевич оформил кредит."
	ds = Decide(priv, Detect(priv))
	masked := false
	for _, d := range ds {
		if d.Type == TFullName && d.Decision == Mask {
			masked = true
		}
	}
	if !masked {
		t.Fatal("client name must be masked")
	}
}

func TestPartialMasks(t *testing.T) {
	if got := PartialMask(TFullName, "Иванов Иван Иванович"); got != "И. И. И." {
		t.Fatalf("name partial: %q", got)
	}
	if got := PartialMask(TPassport, dummyPassport); got != "00** ****00" {
		t.Fatalf("passport partial: %q", got)
	}
}

func makeService(mode Mode) *Service {
	return &Service{Store: NewMemoryStore(), Mode: mode, TTL: time.Minute, Grace: 30 * time.Second}
}

func TestRoundTripAllModes(t *testing.T) {
	text := "Клиент Иванов Иван Иванович оформил кредит, паспорт " + dummyPassport + ", ИНН " + dummyInn12 + "."
	for _, mode := range []Mode{ModePartial, ModeToken, ModeSynthetic} {
		svc := makeService(mode)
		ctx := context.Background()
		masked, dir, err := svc.Process(ctx, text, "rt-"+string(mode))
		if err != nil || dir != "mask" {
			t.Fatalf("%s mask: %v %s", mode, err, dir)
		}
		if strings.Contains(masked, "Иванов Иван Иванович") || strings.Contains(masked, dummyPassport) {
			t.Fatalf("%s left PII: %q", mode, masked)
		}
		back, dir, err := svc.Process(ctx, masked, "rt-"+string(mode))
		if err != nil || dir != "demask" {
			t.Fatalf("%s demask: %v %s", mode, err, dir)
		}
		if back != text {
			t.Fatalf("%s round trip mismatch:\n%q\n%q", mode, text, back)
		}
	}
}

func TestRetryMaskIdempotent(t *testing.T) {
	svc := makeService(ModePartial)
	ctx := context.Background()
	text := "Клиент Петров Сидор Олегович, телефон " + dummyPhone + "."
	m1, _, _ := svc.Process(ctx, text, "idem-1")
	m2, dir, _ := svc.Process(ctx, text, "idem-1")
	if dir != "retry_mask" || m1 != m2 {
		t.Fatalf("retry not idempotent: %s %q vs %q", dir, m1, m2)
	}
}

func TestDemaskRetryDuringGrace(t *testing.T) {
	svc := makeService(ModeToken)
	ctx := context.Background()
	text := "Заявитель Сидорова Анна Петровна указала email test@example.com."
	masked, _, _ := svc.Process(ctx, text, "grace-1")
	d1, _, _ := svc.Process(ctx, masked, "grace-1")
	d2, dir, err := svc.Process(ctx, masked, "grace-1")
	if err != nil || d1 != text || d2 != text || dir != "demask" {
		t.Fatalf("grace retry failed: %v %s %q", err, dir, d2)
	}
}

func TestNoPIIPassthrough(t *testing.T) {
	svc := makeService(ModePartial)
	ctx := context.Background()
	text := "Сегодня хорошая погода, отделение работает до 18:00."
	out, _, err := svc.Process(ctx, text, "nopii-1")
	if err != nil || out != text {
		t.Fatalf("no-PII passthrough broken: %v %q", err, out)
	}
}

func TestConcurrentSamePayloadID(t *testing.T) {
	svc := makeService(ModeSynthetic)
	text := "Клиент Иванов Иван Иванович, карта " + dummyCardSpaced + "."
	var wg sync.WaitGroup
	results := make([]string, 8)
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			out, _, err := svc.Process(context.Background(), text, "race-1")
			if err != nil {
				t.Errorf("worker %d: %v", i, err)
				return
			}
			results[i] = out
		}(i)
	}
	wg.Wait()
	for i := 1; i < 8; i++ {
		if results[i] != results[0] {
			t.Fatalf("divergent results under race:\n%q\n%q", results[0], results[i])
		}
	}
}

func TestSyntheticDeterministic(t *testing.T) {
	text := "Клиент Иванов Иван Иванович, ИНН " + dummyInn12 + "."
	tr1 := BuildTransform(text, Decide(text, Detect(text)), ModeSynthetic, "seed-1")
	tr2 := BuildTransform(text, Decide(text, Detect(text)), ModeSynthetic, "seed-1")
	if tr1.Text != tr2.Text {
		t.Fatal("synthetic transform not deterministic for same seed")
	}
	tr3 := BuildTransform(text, Decide(text, Detect(text)), ModeSynthetic, "seed-2")
	if tr1.Text == tr3.Text {
		t.Fatal("different seeds produced identical synthetics (suspicious)")
	}
}

func TestSyntheticValidChecksums(t *testing.T) {
	text := "Клиент указал ИНН " + dummyInn12 + " и карту " + dummyCardSpaced + "."
	tr := BuildTransform(text, Decide(text, Detect(text)), ModeSynthetic, "s")
	for _, m := range tr.Mappings {
		switch PIIType(m.Type) {
		case TInn:
			if !InnValid(m.Synthetic) {
				t.Fatalf("synthetic INN fails checksum: %s", m.Synthetic)
			}
		case TCardNumber:
			if !LuhnValid(m.Synthetic) {
				t.Fatalf("synthetic card fails Luhn: %s", m.Synthetic)
			}
		}
	}
}

func TestRecordStoresNoPayload(t *testing.T) {
	svc := makeService(ModePartial)
	ctx := context.Background()
	filler := strings.Repeat("Обычное предложение о погоде и природе без имен. ", 2000)
	text := "Клиент Иванов Иван Иванович оформил кредит. " + filler
	if _, _, err := svc.Process(ctx, text, "big-1"); err != nil {
		t.Fatal(err)
	}
	rec, _ := svc.Store.Get(ctx, "big-1")
	if rec == nil {
		t.Fatal("record missing")
	}
	total := 0
	for _, o := range rec.Occurrences {
		total += len(o.Original) + len(o.Masked)
	}
	if total > 5000 {
		t.Fatalf("record stores too much text: %d bytes", total)
	}
}

func TestFreshSetRegressions(t *testing.T) {
	// short_011692: bare patronymic suffix (Ильич) must not break the FIO run
	text := "Клиент Пётр Ильич Чайковский обратился за ипотечным кредитом."
	ds := Decide(text, Detect(text))
	masked := false
	for _, d := range ds {
		if d.Type == TFullName && d.Decision == Mask && strings.Contains(d.Value, "Чайковский") {
			masked = true
		}
	}
	if !masked {
		t.Fatalf("client full name with bare patronymic not masked: %+v", ds)
	}
	// short_006997: issuer must include the city after "г."
	issuer := "Паспорт выдан ГУ МВД России по г. Екатеринбургу, код подразделения 000-000."
	found := false
	for _, c := range Detect(issuer) {
		if c.Type == TPassportIssuer && strings.Contains(c.Value, "Екатеринбургу") {
			found = true
		}
	}
	if !found {
		t.Fatalf("issuer city truncated: %+v", Detect(issuer))
	}
	// short_006354: hyphenated city address
	addr := "Клиент зарегистрирован по адресу г. Санкт-Петербург, ул. Советская, дом 35, квартира 72; заявление принято."
	foundAddr := false
	for _, c := range Detect(addr) {
		if c.Type == TAddress && strings.Contains(c.Value, "Санкт-Петербург") {
			foundAddr = true
		}
	}
	if !foundAddr {
		t.Fatalf("hyphenated-city address missed: %+v", Detect(addr))
	}
	// short_013032: compact address near the keyword
	compact := "Адрес регистрации клиента: Тула, Советская, 75-289."
	foundCompact := false
	for _, c := range Detect(compact) {
		if c.Type == TAddress && strings.Contains(c.Value, "Тула") {
			foundCompact = true
		}
	}
	if !foundCompact {
		t.Fatalf("compact address missed: %+v", Detect(compact))
	}
}

func TestFreshSetRegressionsRound2(t *testing.T) {
	// short_006941: full genitive female FIO
	text := "Заявление поступило от Поповой Ирины Алексеевны."
	masked := false
	for _, d := range Decide(text, Detect(text)) {
		if d.Type == TFullName && d.Decision == Mask && strings.Contains(d.Value, "Поповой Ирины Алексеевны") {
			masked = true
		}
	}
	if !masked {
		t.Fatalf("genitive FIO missed: %+v", Detect(text))
	}
	// short_013294: driver licence 2-2-6
	lic := "Водительское удостоверение клиента: 00 00 000000."
	found := false
	for _, c := range Detect(lic) {
		if c.Type == TDriverLicense {
			found = true
		}
	}
	if !found {
		t.Fatalf("2-2-6 licence missed: %+v", Detect(lic))
	}
	// short_007888: birthplace place captured, not the word "клиента"
	bp := "Место рождения клиента: город Тула; гражданство: Россия."
	okBP := false
	for _, c := range Detect(bp) {
		if c.Type == TBirthPlace {
			if strings.Contains(c.Value, "клиент") {
				t.Fatalf("birthplace captured the role word: %q", c.Value)
			}
			if strings.Contains(c.Value, "Тула") {
				okBP = true
			}
		}
	}
	if !okBP {
		t.Fatalf("birthplace city missed: %+v", Detect(bp))
	}
	// short_011843: org address stays KEEP even when a personal address precedes it
	mixed := "Клиент Иванов Иван Иванович зарегистрирован по адресу 634061, г. Томск, ул. Советская, д. 5, кв. 1; заявление он подал в отделении по адресу 634050, г. Томск, ул. Ленина, д. 40."
	ds := Decide(mixed, Detect(mixed))
	var addrDecisions []Decision
	for _, d := range ds {
		if d.Type == TAddress {
			addrDecisions = append(addrDecisions, d.Decision)
		}
	}
	if len(addrDecisions) != 2 || addrDecisions[0] != Mask || addrDecisions[1] != Keep {
		t.Fatalf("mixed addresses wrong: %v (%+v)", addrDecisions, ds)
	}
}

func TestMaleObliqueFIO(t *testing.T) {
	for _, text := range []string{
		"Заявление поступило от Петрова Николая Ивановича.",
		"Банк направил письмо Иванову Павлу Александровичу.",
	} {
		got := false
		for _, d := range Decide(text, Detect(text)) {
			if d.Type == TFullName && d.Decision == Mask && len(strings.Fields(d.Value)) == 3 {
				got = true
			}
		}
		if !got {
			t.Fatalf("male oblique FIO missed in %q: %+v", text, Detect(text))
		}
	}
}

func TestAdjectivalSurnameFIO(t *testing.T) {
	// Regression (fresh corpus, 124 cases): "Толстой" carries no -ов/-ский
	// suffix, so the FIO run used to stop at "Лев Николаевич" and the client's
	// surname survived partial masking.
	text := "Клиент Лев Николаевич Толстой обратился за ипотечным кредитом и указал телефон " + dummyPhone + "."
	var name string
	for _, d := range Decide(text, Detect(text)) {
		if d.Type == TFullName && d.Decision == Mask {
			name = d.Value
		}
	}
	if name != "Лев Николаевич Толстой" {
		t.Fatalf("adjectival surname not captured: %q", name)
	}
	if got := PartialMask(TFullName, name); got != "Л. Н. Т." {
		t.Fatalf("partial mask: %q", got)
	}
}

func TestCyrillicCardholder(t *testing.T) {
	text := "Клиент указал карту " + dummyCardSpaced + ", держатель ПОПОВ ПАВЕЛ НИКОЛАЕВИЧ."
	found := false
	for _, c := range Detect(text) {
		if c.Type == TCardholder && strings.Contains(c.Value, "ПОПОВ") {
			found = true
		}
	}
	if !found {
		t.Fatalf("cyrillic cardholder missed: %+v", Detect(text))
	}
}
