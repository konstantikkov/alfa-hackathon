package pii

import "strings"

// Contextual privacy engine: port of app/context/{rules,engine}.py.
// Word-start keyword matching with a <=2-char inflection tail.

var personalRoles = []string{
	"клиент", "заемщик", "заёмщик", "заявитель", "страхователь", "застрахованн",
	"держатель", "получатель", "созаемщик", "созаёмщик", "пользователь",
	"вкладчик", "абонент", "истец", "ответчик", "должник", "наследник", "арендатор",
}
var privateActions = []string{
	"обратился", "обратилась", "подал заявку", "подала заявку", "оформил", "оформила",
	"зарегистрирован", "зарегистрирована", "проживает", "проживал", "проживала",
	"сообщил", "сообщила", "указал", "указала",
}
var bankConcepts = []string{"кредит", "ипотек", "договор", "полис", "счет", "счёт", "заявление", "вклад"}
var strongPII = []string{"паспорт", "телефон", "email", "e-mail", "почта", "инн", "карт",
	"cvv", "пин", "дата рождения", "адрес проживания", "адрес регистрации"}
var publicMarkers = []string{"поэт", "писатель", "автор", "ученый", "учёный", "композитор",
	"актер", "актёр", "историческая личность", "написал", "написала", "создал", "создала",
	"произведение", "биография", "роман", "стихотворение", "космонавт", "изобрел", "изобрёл",
	"открыл", "основал", "художник", "картин"}
var orgAddressMarkers = []string{"офис", "отделени", "филиал", "банкомат", "магазин",
	"штаб-квартир", "представительств", "точка продаж", "точке продаж", "пункт выдачи"}
var personalAddressMarkers = []string{"проживает", "проживал", "проживала", "зарегистрирован",
	"зарегистрирована", "адрес клиента", "адрес проживания", "адрес регистрации",
	"домашний адрес", "место жительства", "прописан", "прописана", "должник", "ответчик"}

func isWordByte(b byte) bool {
	return (b >= 'a' && b <= 'z') || (b >= 'A' && b <= 'Z') || (b >= '0' && b <= '9') || b >= 0x80 || b == '-' || b == '_'
}

// keywordHit: word-start match with a bounded (<=4 bytes ~ 2 cyrillic chars) tail.
func keywordHit(windowLower, keyword string) bool {
	idx := 0
	for {
		p := strings.Index(windowLower[idx:], keyword)
		if p < 0 {
			return false
		}
		s := idx + p
		if s == 0 || !isWordByte(windowLower[s-1]) {
			e := s + len(keyword)
			tail := 0
			for e < len(windowLower) && isWordByte(windowLower[e]) {
				e++
				tail++
				if tail > 4 {
					break
				}
			}
			if tail <= 4 {
				return true
			}
		}
		idx = s + 1
	}
}

func countHits(windowLower string, keywords []string) int {
	n := 0
	for _, kw := range keywords {
		if keywordHit(windowLower, kw) {
			n++
		}
	}
	return n
}

func sentenceWindowBounds(text string, start, end, radius int) [2]int {
	lo := start - radius
	if lo < 0 {
		lo = 0
	}
	hi := end + radius
	if hi > len(text) {
		hi = len(text)
	}
	// clip to sentence bounds within the radius
	seg := text[lo:hi]
	relS := start - lo
	relE := end - lo
	sStart := 0
	for i := 0; i < relS; i++ {
		if seg[i] == '.' || seg[i] == '!' || seg[i] == '?' || seg[i] == '\n' {
			sStart = i + 1
		}
	}
	sEnd := len(seg)
	for i := relE; i < len(seg); i++ {
		if seg[i] == '.' || seg[i] == '!' || seg[i] == '?' || seg[i] == '\n' {
			sEnd = i + 1
			break
		}
	}
	return [2]int{lo + sStart, lo + sEnd}
}

// clipToNeighbors shrinks [lo,hi) so the window never crosses into another
// candidate's own span -- otherwise "зарегистрирован" belonging to a first,
// personal address would leak into the org-address next to it (and vice
// versa). Mirrors the python attributed_window.
func clipToNeighbors(lo, hi int, self Candidate, cands []Candidate) (int, int) {
	for _, o := range cands {
		if o.Start == self.Start && o.End == self.End {
			continue
		}
		if o.End <= self.Start && o.End > lo {
			lo = o.End
		}
		if o.Start >= self.End && o.Start < hi {
			hi = o.Start
		}
	}
	return lo, hi
}

func attributedWindow(text string, c Candidate, cands []Candidate) string {
	w := sentenceWindowBounds(text, c.Start, c.End, 220)
	lo, hi := clipToNeighbors(w[0], w[1], c, cands)
	return text[lo:hi]
}

// Decide applies the contextual privacy rules to every candidate.
func Decide(text string, cands []Candidate) []Decided {
	out := make([]Decided, 0, len(cands))
	for _, c := range cands {
		switch c.Type {
		case TFullName:
			out = append(out, decideFullName(text, c, cands))
		case TAddress:
			out = append(out, decideAddress(text, c, cands))
		default:
			out = append(out, Decided{Candidate: c, Decision: Mask, Reasons: []string{"personal_by_construction"}})
		}
	}
	return applyRelationshipRules(text, out)
}

func decideFullName(text string, c Candidate, cands []Candidate) Decided {
	w := strings.ToLower(attributedWindow(text, c, cands))
	private := countHits(w, personalRoles)*2 + countHits(w, privateActions)*2 +
		countHits(w, bankConcepts) + countHits(w, strongPII)*3
	public := countHits(w, publicMarkers) * 2
	if private > 0 {
		return Decided{Candidate: c, Decision: Mask, Reasons: []string{"private_context"}}
	}
	if public > 0 {
		return Decided{Candidate: c, Decision: Keep, Reasons: []string{"public_context"}}
	}
	return Decided{Candidate: c, Decision: Mask, Reasons: []string{"no_context_fail_safe"}}
}

func decideAddress(text string, c Candidate, cands []Candidate) Decided {
	w := strings.ToLower(attributedWindow(text, c, cands))
	if countHits(w, personalAddressMarkers) > 0 {
		return Decided{Candidate: c, Decision: Mask, Reasons: []string{"personal_address"}}
	}
	if countHits(w, orgAddressMarkers) > 0 {
		return Decided{Candidate: c, Decision: Keep, Reasons: []string{"organizational_address"}}
	}
	return Decided{Candidate: c, Decision: Mask, Reasons: []string{"no_context_fail_safe"}}
}

// applyRelationshipRules: PIN masked only when a card number is nearby in the
// same paragraph (port of app/policies/relationship.py behavior).
func applyRelationshipRules(text string, ds []Decided) []Decided {
	cardSpans := make([][2]int, 0, 2)
	for _, d := range ds {
		if d.Type == TCardNumber {
			cardSpans = append(cardSpans, [2]int{d.Start, d.End})
		}
	}
	for i, d := range ds {
		if d.Type != TPin {
			continue
		}
		linked := false
		for _, cs := range cardSpans {
			lo, hi := cs[0], d.End
			if d.Start < cs[0] {
				lo, hi = d.Start, cs[1]
			}
			if hi-lo <= 300 && !strings.Contains(text[lo:hi], "\n\n") {
				linked = true
				break
			}
		}
		if !linked {
			ds[i].Decision = Keep
			ds[i].Reasons = append(ds[i].Reasons, "relationship_rule_unmet:CARD_NUMBER")
		}
	}
	return ds
}
