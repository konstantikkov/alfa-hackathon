package pii

import "strings"

// SpliceMaskedToOriginal restores originals into a payload verified (by
// fingerprint) to be the exact transformed text.
func SpliceMaskedToOriginal(payload string, occ []Occurrence) string {
	var b strings.Builder
	b.Grow(len(payload) + 64)
	cursor := 0
	for _, o := range occ {
		b.WriteString(payload[cursor:o.MaskedStart])
		b.WriteString(o.Original)
		cursor = o.MaskedEnd
	}
	b.WriteString(payload[cursor:])
	return b.String()
}

// SpliceOriginalToMasked re-applies masks to a payload verified to be the
// exact original text (retry of the first request).
func SpliceOriginalToMasked(payload string, occ []Occurrence) string {
	var b strings.Builder
	b.Grow(len(payload) + 64)
	cursor := 0
	for _, o := range occ {
		b.WriteString(payload[cursor:o.OriginalStart])
		b.WriteString(o.Masked)
		cursor = o.OriginalEnd
	}
	b.WriteString(payload[cursor:])
	return b.String()
}

func isWordChar(b byte) bool {
	return (b >= 'a' && b <= 'z') || (b >= 'A' && b <= 'Z') || (b >= '0' && b <= '9') || b == '_' || b >= 0x80
}

// FuzzyDemask handles LLM-modified transformed text: replaces tokens and
// synthetic surfaces (word-bounded) with originals. Unknown tokens are left
// untouched -- we never guess.
func FuzzyDemask(payload string, mappings []Mapping, mode Mode) string {
	if mode == ModePartial {
		return payload // stars are lossy; only exact-splice restores partial
	}
	result := payload
	for _, m := range mappings {
		if m.Token != "" {
			result = strings.ReplaceAll(result, m.Token, m.Original)
		}
	}
	if mode == ModeSynthetic {
		for _, m := range mappings {
			if m.Synthetic == "" {
				continue
			}
			result = replaceWordBounded(result, m.Synthetic, m.Original)
		}
	}
	return result
}

func replaceWordBounded(text, needle, replacement string) string {
	var b strings.Builder
	idx := 0
	for {
		p := strings.Index(text[idx:], needle)
		if p < 0 {
			b.WriteString(text[idx:])
			break
		}
		s := idx + p
		e := s + len(needle)
		okBefore := s == 0 || !isWordChar(text[s-1])
		okAfter := e == len(text) || !isWordChar(text[e])
		if okBefore && okAfter {
			b.WriteString(text[idx:s])
			b.WriteString(replacement)
			idx = e
		} else {
			b.WriteString(text[idx : s+1])
			idx = s + 1
		}
	}
	return b.String()
}
