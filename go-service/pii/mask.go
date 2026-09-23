package pii

import (
	"crypto/sha256"
	"encoding/binary"
	"fmt"
	"sort"
	"strings"
	"unicode"
)

// hashRand draws uniform integers from SHA-256 in counter mode. Surrogates
// must be reproducible for the same (payload_id, entity) so that concurrent
// recomputation of one request always agrees -- a keyed hash stream gives
// that determinism from a cryptographic primitive.
type hashRand struct {
	prefix  []byte
	counter uint64
}

func newHashRand(seed int64) *hashRand {
	return &hashRand{prefix: []byte(fmt.Sprintf("%d:", seed))}
}

// Intn returns a uniform value on [0, n) via rejection sampling.
func (h *hashRand) Intn(n int) int {
	limit := (^uint64(0) / uint64(n)) * uint64(n)
	for {
		payload := append(append([]byte{}, h.prefix...), fmt.Sprintf("%d", h.counter)...)
		h.counter++
		sum := sha256.Sum256(payload)
		value := binary.BigEndian.Uint64(sum[:8])
		if value < limit {
			return int(value % uint64(n))
		}
	}
}

type Mode string

const (
	ModePartial   Mode = "partial"
	ModeToken     Mode = "token"
	ModeSynthetic Mode = "synthetic"
)

// ---- partial masks (port of app/replacement/partial.py) ----------------------

func maskAlnum(v string) string {
	var b strings.Builder
	for _, r := range v {
		if unicode.IsLetter(r) || unicode.IsDigit(r) {
			b.WriteByte('*')
		} else {
			b.WriteRune(r)
		}
	}
	return b.String()
}

func maskName(v string) string {
	parts := strings.Fields(strings.TrimSpace(v))
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		first := '*'
		for _, r := range p {
			if unicode.IsLetter(r) {
				first = unicode.ToUpper(r)
				break
			}
		}
		out = append(out, string(first)+".")
	}
	return strings.Join(out, " ")
}

func maskDigitsKeep(v string, first, last int) string {
	var pos []int
	rs := []rune(v)
	for i, r := range rs {
		if unicode.IsDigit(r) {
			pos = append(pos, i)
		}
	}
	keep := map[int]bool{}
	if len(pos) > first+last {
		for _, i := range pos[:first] {
			keep[i] = true
		}
		for _, i := range pos[len(pos)-last:] {
			keep[i] = true
		}
	} else if first == 1 && len(pos) > 0 { // phone: keep first digit only
		keep[pos[0]] = true
	}
	for _, i := range pos {
		if !keep[i] {
			rs[i] = '*'
		}
	}
	return string(rs)
}

func maskEmail(v string) string {
	at := strings.LastIndex(v, "@")
	if at < 0 {
		return maskAlnum(v)
	}
	return maskAlnum(v[:at]) + "@" + maskAlnum(v[at+1:])
}

func maskPhone(v string) string {
	var pos []int
	rs := []rune(v)
	for i, r := range rs {
		if unicode.IsDigit(r) {
			pos = append(pos, i)
		}
	}
	for k, i := range pos {
		if k != 0 {
			rs[i] = '*'
		}
	}
	return string(rs)
}

func PartialMask(t PIIType, v string) string {
	switch t {
	case TFullName, TCardholder:
		return maskName(v)
	case TEmail:
		return maskEmail(v)
	case TPhone:
		return maskPhone(v)
	case TPassport:
		return maskDigitsKeep(v, 2, 2)
	case TCardNumber:
		return maskDigitsKeep(v, 4, 4)
	case TDriverLicense, TInn:
		return maskDigitsKeep(v, 2, 2)
	default:
		return maskAlnum(v)
	}
}

// ---- deterministic synthetic values ------------------------------------------

var synMaleFirst = []string{"Иван", "Александр", "Дмитрий", "Алексей", "Сергей", "Михаил", "Николай", "Андрей", "Павел", "Максим"}
var synFemaleFirst = []string{"Анна", "Мария", "Елена", "Ольга", "Наталья", "Екатерина", "Ирина", "Алина", "Дарья", "Светлана"}
var synMaleLast = []string{"Иванов", "Петров", "Смирнов", "Кузнецов", "Соколов", "Попов", "Лебедев", "Козлов", "Новиков", "Морозов"}
var synMalePatr = []string{"Иванович", "Александрович", "Сергеевич", "Петрович", "Алексеевич", "Михайлович", "Николаевич", "Андреевич", "Дмитриевич", "Викторович"}
var synFemalePatr = []string{"Ивановна", "Александровна", "Сергеевна", "Петровна", "Алексеевна", "Михайловна", "Николаевна", "Андреевна", "Дмитриевна", "Викторовна"}
var synCities = []string{"Москва", "Санкт-Петербург", "Казань", "Екатеринбург", "Новосибирск", "Самара", "Воронеж", "Пермь"}
var synStreets = []string{"Ленина", "Мира", "Гагарина", "Садовая", "Советская", "Победы", "Лесная", "Центральная"}
var synCountries = []string{"Россия", "Беларусь", "Казахстан", "Армения", "Узбекистан", "Грузия"}

func entitySeed(sessionSeed, identityKey string) int64 {
	h := sha256.Sum256([]byte(sessionSeed + ":" + identityKey))
	return int64(binary.BigEndian.Uint64(h[:8]) & 0x7fffffffffffffff)
}

func synDigitsLike(rng *hashRand, v string, keepPrefix int) string {
	rs := []rune(v)
	seen := 0
	for i, r := range rs {
		if unicode.IsDigit(r) {
			if seen >= keepPrefix {
				rs[i] = rune('0' + rng.Intn(10))
			}
			seen++
		}
	}
	out := string(rs)
	if out == v {
		return synDigitsLike(rng, v, 0)
	}
	return out
}

func synCard(rng *hashRand) string {
	body := []byte{'4'}
	for i := 0; i < 14; i++ {
		body = append(body, byte('0'+rng.Intn(10)))
	}
	return string(body) + string(LuhnCheckDigit(string(body)))
}

func synInn(rng *hashRand, length int) string {
	if length != 12 {
		length = 10
	}
	d := make([]int, length)
	for i := range d {
		d[i] = rng.Intn(10)
	}
	if length == 10 {
		sum := 0
		for i, w := range innW10 {
			sum += d[i] * w
		}
		d[9] = sum % 11 % 10
	} else {
		s11 := 0
		for i, w := range innW11 {
			s11 += d[i] * w
		}
		d[10] = s11 % 11 % 10
		s12 := 0
		for i, w := range innW12 {
			s12 += d[i] * w
		}
		d[11] = s12 % 11 % 10
	}
	var b strings.Builder
	for _, x := range d {
		b.WriteByte(byte('0' + x))
	}
	return b.String()
}

func syntheticFor(rng *hashRand, t PIIType, original, gender string) string {
	switch t {
	case TFullName, TCardholder:
		first, last, patr := synMaleFirst, synMaleLast, synMalePatr
		if gender == "femn" {
			first, patr = synFemaleFirst, synFemalePatr
		}
		l := last[rng.Intn(len(last))]
		if gender == "femn" {
			l += "а"
		}
		n := len(strings.Fields(original))
		switch {
		case n >= 3:
			return l + " " + first[rng.Intn(len(first))] + " " + patr[rng.Intn(len(patr))]
		case n == 2:
			return first[rng.Intn(len(first))] + " " + l
		default:
			return l
		}
	case TEmail:
		return fmt.Sprintf("user%05d@example.com", rng.Intn(100000))
	case TPhone:
		return synDigitsLike(rng, original, 1)
	case TInn:
		digits := 0
		for _, r := range original {
			if unicode.IsDigit(r) {
				digits++
			}
		}
		return synInn(rng, digits)
	case TCardNumber:
		return synCard(rng)
	case TBirthDate, TPassportIssueDate:
		return fmt.Sprintf("%02d.%02d.%d", 1+rng.Intn(28), 1+rng.Intn(12), 1950+rng.Intn(55))
	case TDepartmentCode:
		return fmt.Sprintf("%03d-%03d", rng.Intn(1000), rng.Intn(1000))
	case TCitizenship:
		return synCountries[rng.Intn(len(synCountries))]
	case TPassportIssuer:
		return "ГУ МВД России по г. " + synCities[rng.Intn(len(synCities))]
	case TBirthPlace:
		return "г. " + synCities[rng.Intn(len(synCities))]
	case TAddress:
		return fmt.Sprintf("%06d, г. %s, ул. %s, д. %d, кв. %d",
			100000+rng.Intn(599999), synCities[rng.Intn(len(synCities))],
			synStreets[rng.Intn(len(synStreets))], 1+rng.Intn(250), 1+rng.Intn(500))
	default: // PASSPORT, DRIVER_LICENSE, CVV, PIN
		return synDigitsLike(rng, original, 0)
	}
}

// ---- transform ----------------------------------------------------------------

type Transform struct {
	Text        string
	Mappings    []Mapping
	Occurrences []Occurrence
}

// isNominativeMention: heuristic -- synthetic replacement is only safe when the
// mention is in nominative (we cannot inflect in Go); otherwise TOKEN fallback,
// mirroring the Python low-confidence-morphology policy.
func isNominativeMention(c Candidate) bool {
	if c.Type != TFullName && c.Type != TCardholder {
		return true
	}
	for _, w := range strings.Fields(c.Value) {
		lw := strings.ToLower(strings.TrimRight(w, "."))
		if len([]rune(lw)) <= 2 {
			continue // initials
		}
		if norm := normalizeNameWord(lw); norm != lw {
			return false
		}
	}
	return true
}

func BuildTransform(text string, decided []Decided, mode Mode, sessionSeed string) Transform {
	// group personal decisions by identity key (first-appearance order)
	type group struct {
		id       string
		key      string
		items    []int
		gender   string
		original string
	}
	var groups []*group
	byKey := map[string]*group{}
	counters := map[string]int{}
	var personal []int
	for i, d := range decided {
		if d.Decision == Mask {
			personal = append(personal, i)
		}
	}
	for _, i := range personal {
		d := decided[i]
		key := d.IdentityKey
		if key == "" {
			key = string(d.Type) + ":" + strings.ToLower(strings.TrimSpace(d.Value))
		}
		g, ok := byKey[key]
		if !ok {
			prefix := TokenPrefix(d.Type)
			counters[prefix]++
			g = &group{id: fmt.Sprintf("%s_%d", prefix, counters[prefix]), key: key,
				gender: d.Gender, original: d.Value}
			byKey[key] = g
			groups = append(groups, g)
		}
		g.items = append(g.items, i)
	}

	usedSynthetic := map[PIIType]map[string]bool{}
	mappings := make([]Mapping, 0, len(groups))
	mapByKey := map[string]*Mapping{}
	for _, g := range groups {
		d := decided[g.items[0]]
		used := usedSynthetic[d.Type]
		if used == nil {
			used = map[string]bool{}
			usedSynthetic[d.Type] = used
		}
		var syn string
		for attempt := 0; attempt < 20; attempt++ {
			rng := newHashRand(entitySeed(sessionSeed, g.key) + int64(attempt))
			syn = syntheticFor(rng, d.Type, g.original, g.gender)
			if !used[syn] && syn != g.original {
				break
			}
		}
		used[syn] = true
		m := Mapping{
			EntityID: g.id, Type: string(d.Type), Original: g.original,
			Token: "<" + g.id + ">", Synthetic: syn, Partial: PartialMask(d.Type, g.original),
		}
		mappings = append(mappings, m)
		mapByKey[g.key] = &mappings[len(mappings)-1]
	}

	// build replacements in position order
	type span struct {
		s, e int
		rep  string
		id   string
	}
	var spans []span
	for _, g := range groups {
		m := mapByKey[g.key]
		for _, i := range g.items {
			d := decided[i]
			var rep string
			switch mode {
			case ModePartial:
				rep = PartialMask(d.Type, d.Value)
			case ModeToken:
				rep = m.Token
			default: // synthetic
				if isNominativeMention(d.Candidate) {
					rep = m.Synthetic
				} else {
					rep = m.Token // morphology fallback
				}
			}
			spans = append(spans, span{d.Start, d.End, rep, g.id})
		}
	}
	sort.Slice(spans, func(i, j int) bool { return spans[i].s < spans[j].s })

	var b strings.Builder
	b.Grow(len(text) + 64)
	occ := make([]Occurrence, 0, len(spans))
	cursor, offset := 0, 0
	for _, sp := range spans {
		b.WriteString(text[cursor:sp.s])
		b.WriteString(sp.rep)
		occ = append(occ, Occurrence{
			EntityID: sp.id, OriginalStart: sp.s, OriginalEnd: sp.e,
			MaskedStart: sp.s + offset, MaskedEnd: sp.s + offset + len(sp.rep),
			Original: text[sp.s:sp.e], Masked: sp.rep,
		})
		offset += len(sp.rep) - (sp.e - sp.s)
		cursor = sp.e
	}
	b.WriteString(text[cursor:])

	return Transform{Text: b.String(), Mappings: mappings, Occurrences: occ}
}
