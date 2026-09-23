package pii

import (
	"regexp"
	"strings"
)

// Suffix-heuristic Russian name detection. No pymorphy3-grade analysis exists
// for Go, so: a run of 2-3 capitalized Cyrillic words is a FULL_NAME candidate
// when each word looks like a name part (patronymic/surname suffix or a known
// first name, inflection-tolerant). Identity keys use heuristic nominative
// normalization so repeated inflected mentions group together.

var reCapWord = regexp.MustCompile(`[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?`)
var reInitialsSurname = regexp.MustCompile(`(?:[А-ЯЁ]\.\s?){1,2}[А-ЯЁ][а-яё]+`)
var reSurnameInitials = regexp.MustCompile(`[А-ЯЁ][а-яё]+\s(?:[А-ЯЁ]\.\s?){2}`)

var patrSuffixes = []string{"овичу", "евичу", "овичем", "евичем", "овиче", "евиче", "ович", "евич", "ичем", "ичу", "иче",
	"овича", "евича", "ьича", "ича", "ыча",
	"ьичем", "ьичу", "ьиче", "ьич", "ич", "ыч",
	"овне", "евне", "овною", "евною",
	"овной", "евной", "овну", "евну", "овна", "евна", "ичной", "ичны", "ична", "ичне", "ичну",
	"овны", "евны", "ичны"}
var surnSuffixes = []string{
	"овым", "евым", "иным", "ыным", "овой", "евой", "иной", "ыной", "скому", "цкому", "ского", "цкого", "ским", "цким", "ском", "цком",
	"ову", "еву", "ину", "ыну", "ова", "ева", "ина", "ына", "ове", "еве", "ине", "ыне", "ской", "цкой", "ская", "цкая", "скую", "цкую",
	"ов", "ев", "ин", "ын", "ский", "цкий", "ко", "ук", "юк", "енко",
	// Adjectival surnames (Толстой / Толстая / Толстого / Толстым). Generic
	// adjectives share these endings, but a lone capitalized adjective can
	// never form a candidate: the run builder requires an adjacent first
	// name or patronymic, and hasSuffix demands 3+ chars of stem.
	"ой", "ая", "ую", "ого", "ому", "ым",
}

// Common Russian first names in nominative (male + female); inflected forms
// are matched by stripping 1-2 trailing vowels/endings.
var firstNames = map[string]string{ // name -> gender
	"иван": "masc", "александр": "masc", "дмитрий": "masc", "алексей": "masc", "сергей": "masc",
	"михаил": "masc", "николай": "masc", "андрей": "masc", "павел": "masc", "максим": "masc",
	"антон": "masc", "юрий": "masc", "петр": "masc", "пётр": "masc", "лев": "masc", "роман": "masc",
	"виктор": "masc", "олег": "masc", "игорь": "masc", "владимир": "masc", "евгений": "masc",
	"илья": "masc", "егор": "masc", "тимур": "masc", "марат": "masc", "матвей": "masc",
	"глеб": "masc", "марк": "masc", "степан": "masc", "никита": "masc", "артем": "masc", "артём": "masc",
	"денис": "masc", "кирилл": "masc", "богдан": "masc", "руслан": "masc", "родион": "masc",
	"демид": "masc", "ярослав": "masc", "вячеслав": "masc", "станислав": "masc", "григорий": "masc",
	"анна": "femn", "мария": "femn", "елена": "femn", "ольга": "femn", "наталья": "femn",
	"екатерина": "femn", "ирина": "femn", "алина": "femn", "дарья": "femn", "светлана": "femn",
	"татьяна": "femn", "юлия": "femn", "полина": "femn", "виктория": "femn", "ксения": "femn",
	"вера": "femn", "алиса": "femn", "инна": "femn", "ева": "femn", "лада": "femn", "жанна": "femn",
	"надежда": "femn", "любовь": "femn", "галина": "femn", "валентина": "femn", "маргарита": "femn",
	// fleeting-vowel oblique forms (Павел->Павла etc.) -- suffix stripping cannot recover the nominative
	"павла": "masc", "павлу": "masc", "павлом": "masc", "павле": "masc",
	"льва": "masc", "льву": "masc", "львом": "masc", "льве": "masc",
	"петра": "masc", "петру": "masc", "петром": "masc", "петре": "masc",
	"пётра": "masc", "марка": "masc", "марку": "masc", "марком": "masc",
}

func hasSuffix(w string, suffixes []string) (string, bool) {
	for _, s := range suffixes {
		if strings.HasSuffix(w, s) && len(w) > len(s)+2 {
			return s, true
		}
	}
	return "", false
}

// classifyWord returns (role, gender, ok): role in "first"/"patr"/"surn".
func classifyWord(word string) (string, string, bool) {
	w := strings.ToLower(word)
	if g, ok := firstNames[w]; ok {
		return "first", g, true
	}
	// inflected first name: strip common case endings and retry
	for _, end := range []string{"ой", "ем", "ём", "ом", "ей", "ии", "ию", "ья", "ье", "ью", "ы", "у", "ю", "а", "я", "е", "и"} {
		if strings.HasSuffix(w, end) && len(w) > len(end)+2 {
			base := strings.TrimSuffix(w, end)
			for _, tail := range []string{"", "а", "я", "й", "ий", "ь"} {
				if g, ok := firstNames[base+tail]; ok {
					return "first", g, true
				}
			}
		}
	}
	if s, ok := hasSuffix(w, patrSuffixes); ok {
		g := "masc"
		if strings.Contains(s, "вн") || strings.Contains(s, "чн") {
			g = "femn"
		}
		return "patr", g, true
	}
	if s, ok := hasSuffix(w, surnSuffixes); ok {
		g := "masc"
		if strings.HasSuffix(s, "ая") || strings.HasSuffix(s, "ой") && false || strings.HasSuffix(s, "а") || strings.HasSuffix(s, "у") && false {
			g = ""
		}
		_ = s
		return "surn", g, true
	}
	return "", "", false
}

// normalizeNameWord: heuristic nominative for identity grouping (lowercase).
func normalizeNameWord(word string) string {
	w := strings.ToLower(word)
	if _, ok := firstNames[w]; ok {
		return w
	}
	for _, end := range []string{"ого", "ому", "ой", "ем", "ём", "ом", "ей", "ую", "ая", "ым", "ье", "ья", "ью", "ы", "у", "ю", "е", "и", "а", "я"} {
		if strings.HasSuffix(w, end) && len(w) > len(end)+2 {
			base := strings.TrimSuffix(w, end)
			// first names: try to recover the dictionary form
			for _, tail := range []string{"", "а", "я", "й", "ий", "ь"} {
				if _, ok := firstNames[base+tail]; ok {
					return base + tail
				}
			}
			// surnames/patronymics: strip the case ending; masc surname endings
			// like "ову"->"ов", "овым"->"ов" reduce to the same stem
			if _, ok := hasSuffix(base, surnSuffixes); ok {
				return base
			}
			if _, ok := hasSuffix(base, patrSuffixes); ok {
				return base
			}
			if strings.HasSuffix(base, "ов") || strings.HasSuffix(base, "ев") ||
				strings.HasSuffix(base, "ин") || strings.HasSuffix(base, "ын") ||
				strings.HasSuffix(base, "вич") || strings.HasSuffix(base, "вн") {
				return base
			}
			// adjectival surnames: recover the masculine nominative so that
			// "Толстого"/"Толстым"/"Толстой" all group as one identity
			if strings.HasSuffix(base, "ст") || strings.HasSuffix(base, "цк") || strings.HasSuffix(base, "ск") {
				return base + "ой"
			}
		}
	}
	return w
}

var roleWords = map[string]bool{
	"клиент": true, "банк": true, "заявитель": true, "договор": true, "паспорт": true,
	"россия": true, "москва": true, "отдел": true, "улица": true, "город": true,
}

// DetectFIO finds capitalized-run and initials-form name candidates.
func DetectFIO(text string) []Candidate {
	var out []Candidate
	locs := reCapWord.FindAllStringIndex(text, -1)
	type tok struct {
		s, e         int
		role, gender string
	}
	var toks []tok
	for _, m := range locs {
		w := text[m[0]:m[1]]
		if roleWords[strings.ToLower(w)] {
			continue
		}
		role, gender, ok := classifyWord(w)
		if !ok {
			continue
		}
		toks = append(toks, tok{m[0], m[1], role, gender})
	}
	i := 0
	for i < len(toks) {
		j := i
		for j+1 < len(toks) && j-i < 2 {
			gap := text[toks[j].e:toks[j+1].s]
			if strings.TrimSpace(gap) != "" || len(gap) > 3 {
				break
			}
			j++
		}
		if j > i { // run of 2-3
			run := toks[i : j+1]
			// require at least one non-surname role OR (surname + first)
			roles := map[string]bool{}
			gender := ""
			for _, t := range run {
				roles[t.role] = true
				if gender == "" && t.gender != "" {
					gender = t.gender
				}
			}
			if roles["first"] || roles["patr"] {
				s, e := run[0].s, run[len(run)-1].e
				words := strings.Fields(text[s:e])
				norm := make([]string, len(words))
				for k, w := range words {
					norm[k] = normalizeNameWord(w)
				}
				out = append(out, Candidate{
					Type: TFullName, Value: text[s:e], Start: s, End: e,
					IdentityKey: "person:" + strings.Join(norm, " "),
					Gender:      gender,
				})
				i = j + 1
				continue
			}
		}
		i++
	}
	// initials forms
	for _, re := range []*regexp.Regexp{reInitialsSurname, reSurnameInitials} {
		for _, m := range re.FindAllStringIndex(text, -1) {
			v := strings.TrimRight(text[m[0]:m[1]], " ")
			surname := ""
			for _, w := range strings.Fields(v) {
				if len([]rune(w)) > 2 {
					surname = w
				}
			}
			if surname == "" {
				continue
			}
			if _, ok := hasSuffix(strings.ToLower(surname), surnSuffixes); !ok {
				continue
			}
			out = append(out, Candidate{
				Type: TFullName, Value: v, Start: m[0], End: m[0] + len(v),
				IdentityKey: "person:" + normalizeNameWord(surname),
			})
		}
	}
	return out
}
