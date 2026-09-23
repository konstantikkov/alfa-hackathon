package pii

import (
	"regexp"
	"sort"
	"strings"
)

// Go's RE2 has no lookarounds; digit boundaries are enforced by checking the
// bytes adjacent to each match (ASCII digits, so byte checks are safe).

var (
	reEmail = regexp.MustCompile(`[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,255}\.[A-Za-z]{2,24}`)
	rePhone = regexp.MustCompile(`(?:\+7|8)[\s\-(]{0,2}\d{3}[\s\-)]{0,2}\d{3}[\s\-]?\d{2}[\s\-]?\d{2}`)
	reIntl  = regexp.MustCompile(`\+\d{1,3}(?:[\s\-]?\d{2,4}){2,5}`)
	// longest-first: `\d{10}` would otherwise eat the prefix of a 12-digit INN
	// and fail the digit-boundary check
	reDigitRun10or12 = regexp.MustCompile(`\d{12}|\d{10}`)
	reCard           = regexp.MustCompile(`\d(?:[ \-]?\d){12,18}`)

	rePassSeriesWord = regexp.MustCompile(`(?i)сери[а-яё]*[\s:]+(\d{2}\s?\d{2}|\d{4})[\s,]+(?:номер|н|№)[\s:.]*(\d{6})`)
	rePassNumberSign = regexp.MustCompile(`(\d{4})\s*№\s*(\d{6})`)
	rePassBare46     = regexp.MustCompile(`(\d{4}) (\d{6})`)
	rePassBare226    = regexp.MustCompile(`(\d{2}) (\d{2}) (\d{6})`)

	reDeptCode  = regexp.MustCompile(`\d{3}-\d{3}`)
	reCvvPin    = regexp.MustCompile(`\d{3,4}`)
	reLicense10 = regexp.MustCompile(`\d{10}`)

	reDateNumeric = regexp.MustCompile(`(\d{1,2})([./\-])(\d{1,2})[./\-](\d{4})`)
	reDateISO     = regexp.MustCompile(`(\d{4})-(\d{1,2})-(\d{1,2})`)
	reDateText    = regexp.MustCompile(`(?i)\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+\d{4}(?:\s+года)?`)

	reAddrIndex = regexp.MustCompile(`\d{6},?\s*(?:г\.|город)\s*[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё][а-яё]+)?,?\s*(?:ул\.|улица|пр-т|проспект|пер\.|переулок)\s*[А-ЯЁ][а-яё\-]+(?:\s[А-ЯЁа-яё\-]+)?,?\s*(?:д\.|дом)\s*\d+[а-яА-Я]?(?:,?\s*(?:кв\.|квартира|корп\.|корпус|стр\.)\s*\d+)*`)
	reAddrWords = regexp.MustCompile(`(?:г\.|город)\s*[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё][а-яё]+)?,?\s*(?:ул\.|улица|пр-т|проспект)\s*[А-ЯЁ][а-яё\-]+(?:\s[А-ЯЁа-яё\-]+)?,?\s*(?:д\.|дом)\s*\d+[а-яА-Я]?(?:,?\s*(?:кв\.|квартира)\s*\d+)?`)

	reAddrCompact = regexp.MustCompile(`[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё][а-яё]+)?,\s*[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё][а-яё]+)?(?:\s[а-яё]+)?,\s*\d+(?:-\d+)?`)
	reBirthPlace  = regexp.MustCompile(`(?:(?i:место\s+рождения|родил(?:ся|ась)))(?:\s+[а-яё]+)?\s*:?\s*(?:(?i:в)\s+)?((?:г\.|гор\.|город)\s*[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё][а-яё]+)?|[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё][а-яё]+)?(?:\s*,\s*[А-ЯЁ][а-яё\-]+)?)`)
	// RE2 has no lookahead: run to a clause terminator (dots allowed -- issuers
	// contain "г."/"р-на"), then trim trailing punctuation.
	reIssuer     = regexp.MustCompile(`(?:ГУ\s+МВД|УМВД|ОУФМС|УФМС|Отделом?\s+(?:УФМС|МВД|ОВД))\s+[Рр]оссии\s+по\s+[^,;\n]+`)
	reCardholder = regexp.MustCompile(`[A-Z]{2,20}\s[A-Z]{2,20}(?:\s[A-Z]{2,20})?|[А-ЯЁ]{2,20}\s[А-ЯЁ]{2,20}(?:\s[А-ЯЁ]{2,20})?`)

	citizenships = []string{
		"Российская Федерация", "Россия", "Беларусь", "Казахстан", "Армения",
		"Кыргызстан", "Узбекистан", "Таджикистан", "Азербайджан", "Молдова",
		"Туркменистан", "Грузия", "РФ",
	}
)

func isDigitByte(b byte) bool { return b >= '0' && b <= '9' }

// digitBounded reports whether text[start:end] is not adjacent to more digits.
func digitBounded(text string, start, end int) bool {
	if start > 0 && isDigitByte(text[start-1]) {
		return false
	}
	if end < len(text) && isDigitByte(text[end]) {
		return false
	}
	return true
}

func window(text string, start, end, radius int) string {
	lo := start - radius
	if lo < 0 {
		lo = 0
	}
	hi := end + radius
	if hi > len(text) {
		hi = len(text)
	}
	return text[lo:hi]
}

func windowHasAny(text string, start, end, radius int, needles ...string) bool {
	w := strings.ToLower(window(text, start, end, radius))
	for _, n := range needles {
		if strings.Contains(w, n) {
			return true
		}
	}
	return false
}

// Detect runs every detector and returns overlap-resolved candidates.
func Detect(text string) []Candidate {
	var out []Candidate
	out = append(out, detectEmail(text)...)
	out = append(out, detectPhone(text)...)
	out = append(out, detectInn(text)...)
	out = append(out, detectCard(text)...)
	out = append(out, detectPassport(text)...)
	out = append(out, detectDeptCode(text)...)
	out = append(out, detectCvvPin(text)...)
	out = append(out, detectLicense(text)...)
	out = append(out, detectDates(text)...)
	out = append(out, detectAddress(text)...)
	out = append(out, detectBirthPlace(text)...)
	out = append(out, detectCitizenship(text)...)
	out = append(out, detectIssuer(text)...)
	out = append(out, detectCardholder(text)...)
	out = append(out, DetectFIO(text)...)
	return resolveOverlaps(out)
}

func detectEmail(text string) []Candidate {
	if !strings.Contains(text, "@") {
		return nil
	}
	var out []Candidate
	for _, m := range reEmail.FindAllStringIndex(text, -1) {
		out = append(out, Candidate{Type: TEmail, Value: text[m[0]:m[1]], Start: m[0], End: m[1]})
	}
	return out
}

func detectPhone(text string) []Candidate {
	var out []Candidate
	seen := map[int]bool{}
	for _, m := range rePhone.FindAllStringIndex(text, -1) {
		if digitBounded(text, m[0], m[1]) {
			out = append(out, Candidate{Type: TPhone, Value: text[m[0]:m[1]], Start: m[0], End: m[1]})
			seen[m[0]] = true
		}
	}
	for _, m := range reIntl.FindAllStringIndex(text, -1) {
		if !seen[m[0]] && digitBounded(text, m[0], m[1]) {
			out = append(out, Candidate{Type: TPhone, Value: text[m[0]:m[1]], Start: m[0], End: m[1]})
		}
	}
	return out
}

func detectInn(text string) []Candidate {
	var out []Candidate
	for _, m := range reDigitRun10or12.FindAllStringIndex(text, -1) {
		if !digitBounded(text, m[0], m[1]) {
			continue
		}
		v := text[m[0]:m[1]]
		if InnValid(v) && windowHasAny(text, m[0], m[1], 60, "инн", "налогоплательщик") {
			out = append(out, Candidate{Type: TInn, Value: v, Start: m[0], End: m[1]})
		}
	}
	return out
}

func detectCard(text string) []Candidate {
	var out []Candidate
	for _, m := range reCard.FindAllStringIndex(text, -1) {
		if !digitBounded(text, m[0], m[1]) {
			continue
		}
		v := text[m[0]:m[1]]
		if LuhnValid(v) {
			out = append(out, Candidate{Type: TCardNumber, Value: v, Start: m[0], End: m[1]})
		}
	}
	return out
}

func detectPassport(text string) []Candidate {
	var out []Candidate
	add := func(m []int) {
		if digitBounded(text, m[0], m[1]) {
			out = append(out, Candidate{Type: TPassport, Value: text[m[0]:m[1]], Start: m[0], End: m[1]})
		}
	}
	for _, m := range rePassSeriesWord.FindAllStringIndex(text, -1) {
		add(m)
	}
	for _, m := range rePassNumberSign.FindAllStringIndex(text, -1) {
		add(m)
	}
	for _, re := range []*regexp.Regexp{rePassBare46, rePassBare226} {
		for _, m := range re.FindAllStringIndex(text, -1) {
			if digitBounded(text, m[0], m[1]) &&
				windowHasAny(text, m[0], m[1], 80, "паспорт") {
				add(m)
			}
		}
	}
	return out
}

func detectDeptCode(text string) []Candidate {
	var out []Candidate
	for _, m := range reDeptCode.FindAllStringIndex(text, -1) {
		if digitBounded(text, m[0], m[1]) &&
			windowHasAny(text, m[0], m[1], 80, "код подразделения", "подразделени") {
			out = append(out, Candidate{Type: TDepartmentCode, Value: text[m[0]:m[1]], Start: m[0], End: m[1]})
		}
	}
	return out
}

func detectCvvPin(text string) []Candidate {
	var out []Candidate
	for _, m := range reCvvPin.FindAllStringIndex(text, -1) {
		if !digitBounded(text, m[0], m[1]) {
			continue
		}
		v := text[m[0]:m[1]]
		if windowHasAny(text, m[0], m[1], 40, "cvv", "cvc", "cvv2") {
			out = append(out, Candidate{Type: TCvv, Value: v, Start: m[0], End: m[1]})
		} else if len(v) == 4 && windowHasAny(text, m[0], m[1], 40, "пин", "pin") {
			out = append(out, Candidate{Type: TPin, Value: v, Start: m[0], End: m[1]})
		}
	}
	return out
}

func detectLicense(text string) []Candidate {
	var out []Candidate
	consume := func(m []int) bool {
		return digitBounded(text, m[0], m[1]) &&
			windowHasAny(text, m[0], m[1], 90, "водительск", "удостоверен", "в/у")
	}
	for _, re := range []*regexp.Regexp{rePassBare46, rePassBare226} {
		for _, m := range re.FindAllStringIndex(text, -1) {
			if consume(m) {
				out = append(out, Candidate{Type: TDriverLicense, Value: text[m[0]:m[1]], Start: m[0], End: m[1]})
			}
		}
	}
	for _, m := range reLicense10.FindAllStringIndex(text, -1) {
		if consume(m) {
			out = append(out, Candidate{Type: TDriverLicense, Value: text[m[0]:m[1]], Start: m[0], End: m[1]})
		}
	}
	return out
}

func detectDates(text string) []Candidate {
	var out []Candidate
	classify := func(m []int) {
		if !digitBounded(text, m[0], m[1]) {
			return
		}
		if windowHasAny(text, m[0], m[1], 90, "рожден", "родил", "дата рождения", "д.р.") {
			out = append(out, Candidate{Type: TBirthDate, Value: text[m[0]:m[1]], Start: m[0], End: m[1]})
		} else if windowHasAny(text, m[0], m[1], 90, "выдан", "дата выдачи") {
			out = append(out, Candidate{Type: TPassportIssueDate, Value: text[m[0]:m[1]], Start: m[0], End: m[1]})
		}
		// ordinary dates are deliberately not candidates (spec: keep as is)
	}
	for _, re := range []*regexp.Regexp{reDateNumeric, reDateISO, reDateText} {
		for _, m := range re.FindAllStringIndex(text, -1) {
			classify(m)
		}
	}
	return out
}

func detectAddress(text string) []Candidate {
	var out []Candidate
	for _, re := range []*regexp.Regexp{reAddrIndex, reAddrWords} {
		for _, m := range re.FindAllStringIndex(text, -1) {
			out = append(out, Candidate{Type: TAddress, Value: text[m[0]:m[1]], Start: m[0], End: m[1]})
		}
	}
	// compact "Город, Улица, 75-289" only next to an explicit address keyword
	for _, m := range reAddrCompact.FindAllStringIndex(text, -1) {
		if windowHasAny(text, m[0], m[1], 60, "адрес") {
			out = append(out, Candidate{Type: TAddress, Value: text[m[0]:m[1]], Start: m[0], End: m[1]})
		}
	}
	return out
}

func detectBirthPlace(text string) []Candidate {
	var out []Candidate
	for _, m := range reBirthPlace.FindAllStringSubmatchIndex(text, -1) {
		s, e := m[2], m[3]
		if s >= 0 {
			out = append(out, Candidate{Type: TBirthPlace, Value: text[s:e], Start: s, End: e})
		}
	}
	return out
}

func detectCitizenship(text string) []Candidate {
	var out []Candidate
	lower := strings.ToLower(text)
	if !strings.Contains(lower, "гражданств") {
		return nil
	}
	for _, c := range citizenships {
		idx := 0
		for {
			p := strings.Index(text[idx:], c)
			if p < 0 {
				break
			}
			s := idx + p
			e := s + len(c)
			if windowHasAny(text, s, e, 60, "гражданств") {
				out = append(out, Candidate{Type: TCitizenship, Value: c, Start: s, End: e})
			}
			idx = e
		}
	}
	return out
}

func detectIssuer(text string) []Candidate {
	var out []Candidate
	for _, m := range reIssuer.FindAllStringIndex(text, -1) {
		v := strings.TrimRight(text[m[0]:m[1]], " \t.")
		out = append(out, Candidate{Type: TPassportIssuer, Value: v, Start: m[0], End: m[0] + len(v)})
	}
	return out
}

func detectCardholder(text string) []Candidate {
	var out []Candidate
	for _, m := range reCardholder.FindAllStringIndex(text, -1) {
		if windowHasAny(text, m[0], m[1], 80, "карт", "держатель", "card") {
			out = append(out, Candidate{Type: TCardholder, Value: text[m[0]:m[1]], Start: m[0], End: m[1]})
		}
	}
	return out
}

// resolveOverlaps: sort by (start, -len), greedy keep non-overlapping.
func resolveOverlaps(cands []Candidate) []Candidate {
	if len(cands) < 2 {
		return cands
	}
	sortCandidates(cands)
	out := cands[:0]
	lastEnd := -1
	for _, c := range cands {
		if c.Start >= lastEnd {
			out = append(out, c)
			lastEnd = c.End
		}
	}
	return out
}

func sortCandidates(cands []Candidate) {
	sort.Slice(cands, func(i, j int) bool {
		if cands[i].Start != cands[j].Start {
			return cands[i].Start < cands[j].Start
		}
		return (cands[i].End - cands[i].Start) > (cands[j].End - cands[j].Start)
	})
}
