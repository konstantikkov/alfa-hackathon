package pii

import (
	"context"
	"regexp"
	"strings"
	"time"
)

// Service implements the /process contract semantics (mirror of
// app/core/process_service.py): direction inferred from fingerprints,
// idempotent retries, fail-closed on storage loss for reversible mappings.
type Service struct {
	Store  Store
	Mode   Mode
	Secret string
	TTL    time.Duration
	Grace  time.Duration
}

var tokenTrace = regexp.MustCompile(`<[A-Z_]+_\d+>`)

func (s *Service) Process(ctx context.Context, payload, payloadID string) (string, string, error) {
	record, err := s.Store.Get(ctx, payloadID)
	if err != nil {
		return s.answerWithoutStorage(payload)
	}
	if record == nil {
		return s.firstRequest(ctx, payload, payloadID)
	}

	fp := Fingerprint(payload, s.Secret)
	if fp == record.OriginalFingerprint {
		return SpliceOriginalToMasked(payload, record.Occurrences), "retry_mask", nil
	}
	if fp == record.TransformedFingerprint {
		result := SpliceMaskedToOriginal(payload, record.Occurrences)
		if record.State != StateDemaskedGrace {
			_ = s.Store.MarkDemasked(ctx, payloadID, s.Grace)
		}
		return result, "demask", nil
	}
	// LLM-modified transformed text
	result := FuzzyDemask(payload, record.Mappings, Mode(record.Mode))
	if record.State != StateDemaskedGrace {
		_ = s.Store.MarkDemasked(ctx, payloadID, s.Grace)
	}
	return result, "demask", nil
}

func (s *Service) firstRequest(ctx context.Context, payload, payloadID string) (string, string, error) {
	cands := Detect(payload)
	decided := Decide(payload, cands)
	tr := BuildTransform(payload, decided, s.Mode, payloadID)
	record := &Record{
		Version: 2, State: StateActive, Mode: string(s.Mode),
		OriginalFingerprint:    Fingerprint(payload, s.Secret),
		TransformedFingerprint: Fingerprint(tr.Text, s.Secret),
		NoPII:                  len(tr.Mappings) == 0,
		Mappings:               tr.Mappings,
		Occurrences:            tr.Occurrences,
		CreatedAt:              float64(time.Now().Unix()),
	}
	if _, err := s.Store.SaveIfAbsent(ctx, payloadID, record, s.TTL); err != nil {
		if record.NoPII {
			return tr.Text, "mask_degraded", nil // nothing reversible was created
		}
		return "", "", ErrStorageUnavailable // fail closed
	}
	return tr.Text, "mask", nil
}

// answerWithoutStorage mirrors the Python degraded path: only requests that
// need no reversible state (no PII, no mask traces) are answered.
func (s *Service) answerWithoutStorage(payload string) (string, string, error) {
	if tokenTrace.MatchString(payload) || strings.Contains(payload, "**") {
		return "", "", ErrStorageUnavailable
	}
	cands := Detect(payload)
	decided := Decide(payload, cands)
	tr := BuildTransform(payload, decided, s.Mode, "degraded")
	if len(tr.Mappings) > 0 {
		return "", "", ErrStorageUnavailable
	}
	return payload, "mask_degraded", nil
}
