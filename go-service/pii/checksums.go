package pii

// Luhn validity check over the digits of s (separators ignored).
func LuhnValid(s string) bool {
	var digits []int
	for _, ch := range s {
		if ch >= '0' && ch <= '9' {
			digits = append(digits, int(ch-'0'))
		}
	}
	if len(digits) < 13 || len(digits) > 19 {
		return false
	}
	sum := 0
	parity := len(digits) % 2
	for i, d := range digits {
		if i%2 == parity {
			d *= 2
			if d > 9 {
				d -= 9
			}
		}
		sum += d
	}
	return sum%10 == 0
}

func LuhnCheckDigit(prefix string) byte {
	digits := make([]int, 0, len(prefix)+1)
	for _, ch := range prefix {
		digits = append(digits, int(ch-'0'))
	}
	digits = append(digits, 0)
	sum := 0
	parity := len(digits) % 2
	for i, d := range digits {
		if i%2 == parity {
			d *= 2
			if d > 9 {
				d -= 9
			}
		}
		sum += d
	}
	return byte('0' + (10-sum%10)%10)
}

var innW11 = []int{7, 2, 4, 10, 3, 5, 9, 4, 6, 8}
var innW12 = []int{3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8}
var innW10 = []int{2, 4, 10, 3, 5, 9, 4, 6, 8}

func InnValid(s string) bool {
	d := make([]int, 0, 12)
	for _, ch := range s {
		if ch >= '0' && ch <= '9' {
			d = append(d, int(ch-'0'))
		}
	}
	switch len(d) {
	case 10:
		sum := 0
		for i, w := range innW10 {
			sum += d[i] * w
		}
		return sum%11%10 == d[9]
	case 12:
		s11 := 0
		for i, w := range innW11 {
			s11 += d[i] * w
		}
		if s11%11%10 != d[10] {
			return false
		}
		s12 := 0
		for i, w := range innW12 {
			s12 += d[i] * w
		}
		return s12%11%10 == d[11]
	}
	return false
}
