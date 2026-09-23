package pii

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
)

// Fingerprint matches app/core/hashing.py: HMAC-SHA-256 when a server secret
// is configured, plain SHA-256 baseline otherwise.
func Fingerprint(value, secret string) string {
	if secret != "" {
		mac := hmac.New(sha256.New, []byte(secret))
		mac.Write([]byte(value))
		return hex.EncodeToString(mac.Sum(nil))
	}
	sum := sha256.Sum256([]byte(value))
	return hex.EncodeToString(sum[:])
}
