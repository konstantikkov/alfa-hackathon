package pii

import (
	"math"
	"sync"
	"time"
)

// Admission: bounded in-process backpressure with dynamic Retry-After
// (port of app/admission/controller.py, simplified to the load-bearing parts).
type Admission struct {
	mu             sync.Mutex
	maxInFlight    int
	maxBacklog     float64
	baseCost       float64
	perChar        float64
	minRetry       float64
	maxRetry       float64
	safety         float64
	inFlight       int
	outstanding    float64
	arrivalRate    float64 // EWMA work units/sec
	serviceRate    float64
	lastArrival    time.Time
	lastCompletion time.Time
}

func NewAdmission(maxInFlight int, maxBacklog float64) *Admission {
	return &Admission{
		maxInFlight: maxInFlight, maxBacklog: maxBacklog,
		baseCost: 500, perChar: 1, minRetry: 1, maxRetry: 30, safety: 1.5,
	}
}

type Rejection struct {
	RetryAfter float64
	Reason     string
}

func (a *Admission) TryAcquire(payloadSize int) (float64, *Rejection) {
	work := a.baseCost + a.perChar*float64(payloadSize)
	a.mu.Lock()
	defer a.mu.Unlock()
	now := time.Now()
	if !a.lastArrival.IsZero() {
		dt := math.Max(now.Sub(a.lastArrival).Seconds(), 0.001)
		a.arrivalRate = 0.2*(work/dt) + 0.8*a.arrivalRate
	}
	a.lastArrival = now

	if a.inFlight >= a.maxInFlight {
		return 0, &Rejection{RetryAfter: a.retryAfterLocked(work), Reason: "in_flight_limit"}
	}
	if a.outstanding+work > a.maxBacklog {
		return 0, &Rejection{RetryAfter: a.retryAfterLocked(work), Reason: "backlog_limit"}
	}
	a.inFlight++
	a.outstanding += work
	return work, nil
}

func (a *Admission) Release(work float64) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.inFlight--
	if a.inFlight < 0 {
		a.inFlight = 0
	}
	a.outstanding -= work
	if a.outstanding < 0 {
		a.outstanding = 0
	}
	now := time.Now()
	if !a.lastCompletion.IsZero() {
		dt := math.Max(now.Sub(a.lastCompletion).Seconds(), 0.001)
		a.serviceRate = 0.2*(work/dt) + 0.8*a.serviceRate
	}
	a.lastCompletion = now
}

func (a *Admission) retryAfterLocked(incoming float64) float64 {
	var base float64
	if a.serviceRate > 0 {
		base = (a.outstanding + incoming) / a.serviceRate
	} else {
		base = a.minRetry
	}
	pressure := 1.0
	if a.serviceRate > 0 && a.arrivalRate > a.serviceRate {
		pressure = a.arrivalRate / a.serviceRate
	}
	effectiveMin := math.Min(a.minRetry*math.Max(1, pressure/2), a.maxRetry/3)
	retry := base * pressure * a.safety
	return math.Round(math.Min(math.Max(retry, effectiveMin), a.maxRetry)*10) / 10
}

func (a *Admission) InFlight() int {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.inFlight
}
