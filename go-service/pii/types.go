// Package pii is the Go port of the PII Security Proxy hot core.
//
// Scope vs the Python reference implementation: full /process contract
// (fingerprint idempotency, partial/token/synthetic modes, Mongo+Redis
// resilient storage, admission control), all structured detectors, keyword
// context engine. Russian FIO handling uses suffix-heuristic morphology
// (there is no pymorphy3-grade analyzer in Go): partial (initials) and token
// modes are exact; synthetic names are generated in nominative only and
// case-inflected mentions fall back to TOKEN, mirroring the Python service's
// own low-confidence-morphology fallback. The ML fallback is not ported
// (disabled by default in the reference too).
package pii

type PIIType string

const (
	TFullName          PIIType = "FULL_NAME"
	TBirthDate         PIIType = "BIRTH_DATE"
	TBirthPlace        PIIType = "BIRTH_PLACE"
	TPassport          PIIType = "PASSPORT"
	TCitizenship       PIIType = "CITIZENSHIP"
	TPassportIssuer    PIIType = "PASSPORT_ISSUER"
	TDepartmentCode    PIIType = "DEPARTMENT_CODE"
	TPassportIssueDate PIIType = "PASSPORT_ISSUE_DATE"
	TDriverLicense     PIIType = "DRIVER_LICENSE"
	TAddress           PIIType = "ADDRESS"
	TEmail             PIIType = "EMAIL"
	TPhone             PIIType = "PHONE"
	TInn               PIIType = "INN"
	TCardNumber        PIIType = "CARD_NUMBER"
	TCvv               PIIType = "CVV"
	TPin               PIIType = "PIN"
	TCardholder        PIIType = "CARDHOLDER"
)

// tokenPrefix mirrors app/replacement/tokens.py.
var tokenPrefix = map[PIIType]string{
	TFullName:   "PERSON",
	TCardNumber: "CARD",
}

func TokenPrefix(t PIIType) string {
	if p, ok := tokenPrefix[t]; ok {
		return p
	}
	return string(t)
}

type Candidate struct {
	Type       PIIType
	Value      string
	Start, End int // byte offsets
	// identity metadata for FULL_NAME grouping
	IdentityKey string
	Gender      string // "masc"/"femn"/""
}

type Decision int

const (
	Keep Decision = iota
	Mask
)

type Decided struct {
	Candidate
	Decision Decision
	Reasons  []string
}

type Mapping struct {
	EntityID  string `json:"entity_id"`
	Type      string `json:"type"`
	Original  string `json:"original"`
	Token     string `json:"token"`
	Synthetic string `json:"synthetic"`
	Partial   string `json:"partial"`
}

type Occurrence struct {
	EntityID      string `json:"entity_id"`
	OriginalStart int    `json:"original_start"`
	OriginalEnd   int    `json:"original_end"`
	MaskedStart   int    `json:"masked_start"`
	MaskedEnd     int    `json:"masked_end"`
	Original      string `json:"original"`
	Masked        string `json:"masked"`
}

// Record is the persisted mapping state -- fingerprints + per-occurrence
// spans, never the documents themselves (same schema family as the Python
// service, version 2).
type Record struct {
	Version                int          `json:"version" bson:"version"`
	State                  string       `json:"state" bson:"state"`
	Mode                   string       `json:"mode" bson:"mode"`
	OriginalFingerprint    string       `json:"original_fingerprint" bson:"original_fingerprint"`
	TransformedFingerprint string       `json:"transformed_fingerprint" bson:"transformed_fingerprint"`
	NoPII                  bool         `json:"no_pii" bson:"no_pii"`
	Mappings               []Mapping    `json:"mappings" bson:"mappings"`
	Occurrences            []Occurrence `json:"occurrences" bson:"occurrences"`
	CreatedAt              float64      `json:"created_at" bson:"created_at"`
	DemaskedAt             float64      `json:"demasked_at,omitempty" bson:"demasked_at,omitempty"`
}

const (
	StateActive        = "ACTIVE"
	StateDemaskedGrace = "DEMASKED_GRACE"
)
