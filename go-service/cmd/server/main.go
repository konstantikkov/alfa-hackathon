package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"piigo/pii"
)

// detailKey is the error-payload field name shared by every non-200 response.
const detailKey = "detail"

var (
	reqCount = prometheus.NewCounterVec(prometheus.CounterOpts{
		Name: "pii_requests_total", Help: "Total /process requests",
	}, []string{"direction", "outcome"})
	reqLatency = prometheus.NewHistogramVec(prometheus.HistogramOpts{
		Name: "pii_request_latency_seconds", Help: "End-to-end /process latency",
	}, []string{"direction"})
	rejected = prometheus.NewCounterVec(prometheus.CounterOpts{
		Name: "pii_admission_rejected_total", Help: "429 responses",
	}, []string{"reason"})
)

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

type processReq struct {
	Payload   string `json:"payload"`
	PayloadID string `json:"payload_id"`
}

func main() {
	prometheus.MustRegister(reqCount, reqLatency, rejected)

	var store pii.Store
	var cache *pii.RedisStore
	if url := os.Getenv("PII_REDIS_URL"); url != "" {
		if c, err := pii.NewRedisStore(url); err == nil {
			cache = c
		} else {
			log.Printf("redis unavailable: %v", err)
		}
	}
	mongoURL := os.Getenv("PII_MONGO_URL")
	if mongoURL != "" {
		m, err := pii.NewMongoStore(mongoURL, env("PII_MONGO_DB", "pii_go"), env("PII_MONGO_COLLECTION", "mappings"))
		if err != nil {
			log.Fatalf("mongo init: %v", err)
		}
		store = pii.NewResilientStore(m, cache, 300*time.Second)
	} else if cache != nil {
		store = pii.NewResilientStore(cache, nil, 300*time.Second)
	} else {
		log.Printf("no PII_MONGO_URL/PII_REDIS_URL: in-memory store (dev only)")
		store = pii.NewMemoryStore()
	}

	ttl, _ := strconv.Atoi(env("PII_MAPPING_TTL_SECONDS", "600"))
	grace, _ := strconv.Atoi(env("PII_DEMASK_RETRY_GRACE_SECONDS", "60"))
	maxInFlight, _ := strconv.Atoi(env("PII_ADMISSION_MAX_IN_FLIGHT", "256"))
	maxPayload, _ := strconv.Atoi(env("PII_MAX_PAYLOAD_BYTES", "3000000"))

	svc := &pii.Service{
		Store:  store,
		Mode:   pii.Mode(env("PII_DEFAULT_MASKING_MODE", "partial")),
		Secret: os.Getenv("PII_FINGERPRINT_SECRET"),
		TTL:    time.Duration(ttl) * time.Second,
		Grace:  time.Duration(grace) * time.Second,
	}
	adm := pii.NewAdmission(maxInFlight, 16_000_000)

	mux := http.NewServeMux()
	mux.Handle("/metrics", promhttp.Handler())
	mux.HandleFunc("/health", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"status":"ok","engine":"go","in_flight":%d}`, adm.InFlight())
	})
	mux.HandleFunc("/process", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		if r.ContentLength > int64(maxPayload) {
			writeJSON(w, http.StatusRequestEntityTooLarge, map[string]string{detailKey: "payload too large"})
			return
		}
		work, rej := adm.TryAcquire(int(r.ContentLength))
		if rej != nil {
			rejected.WithLabelValues(rej.Reason).Inc()
			w.Header().Set("Retry-After", strconv.Itoa(int(rej.RetryAfter+0.999)))
			writeJSON(w, http.StatusTooManyRequests, map[string]string{detailKey: "over capacity, retry later"})
			return
		}
		defer adm.Release(work)

		started := time.Now()
		var body processReq
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.PayloadID == "" {
			writeJSON(w, http.StatusUnprocessableEntity, map[string]string{detailKey: "invalid request"})
			return
		}
		result, direction, err := svc.Process(r.Context(), body.Payload, body.PayloadID)
		latency := time.Since(started).Seconds()
		if err != nil {
			if errors.Is(err, pii.ErrStorageUnavailable) {
				reqCount.WithLabelValues(direction, "error").Inc()
				w.Header().Set("Retry-After", "5")
				writeJSON(w, http.StatusServiceUnavailable, map[string]string{detailKey: "mapping storage unavailable"})
				return
			}
			reqCount.WithLabelValues(direction, "error").Inc()
			writeJSON(w, http.StatusInternalServerError, map[string]string{detailKey: "internal error"})
			return
		}
		reqCount.WithLabelValues(direction, "success").Inc()
		reqLatency.WithLabelValues(direction).Observe(latency)
		writeJSON(w, http.StatusOK, map[string]string{"result": result})
	})

	addr := ":" + env("PORT", "8090")
	log.Printf("pii-go listening on %s", addr)
	srv := &http.Server{
		Addr: addr, Handler: mux,
		ReadTimeout: 15 * time.Second, WriteTimeout: 15 * time.Second,
		ReadHeaderTimeout: 5 * time.Second, // gosec G112: Slowloris guard
	}
	log.Fatal(srv.ListenAndServe())
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
