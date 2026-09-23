package pii

import (
	"context"
	"encoding/json"
	"errors"
	"sync"
	"sync/atomic"
	"time"

	"github.com/redis/go-redis/v9"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/mongo"
	"go.mongodb.org/mongo-driver/mongo/options"
)

var ErrStorageUnavailable = errors.New("mapping storage unavailable")

type Store interface {
	Get(ctx context.Context, payloadID string) (*Record, error) // (nil, nil) = not found
	SaveIfAbsent(ctx context.Context, payloadID string, r *Record, ttl time.Duration) (bool, error)
	MarkDemasked(ctx context.Context, payloadID string, grace time.Duration) error
}

// ---- in-memory (tests / dev fallback) ----------------------------------------

type MemoryStore struct {
	mu   sync.Mutex
	data map[string]memEntry
}

type memEntry struct {
	expires time.Time
	record  Record
}

func NewMemoryStore() *MemoryStore { return &MemoryStore{data: map[string]memEntry{}} }

func (m *MemoryStore) Get(_ context.Context, id string) (*Record, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	e, ok := m.data[id]
	if !ok || time.Now().After(e.expires) {
		delete(m.data, id)
		return nil, nil
	}
	r := e.record
	return &r, nil
}

func (m *MemoryStore) SaveIfAbsent(_ context.Context, id string, r *Record, ttl time.Duration) (bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if e, ok := m.data[id]; ok && time.Now().Before(e.expires) {
		return false, nil
	}
	m.data[id] = memEntry{expires: time.Now().Add(ttl), record: *r}
	return true, nil
}

func (m *MemoryStore) MarkDemasked(_ context.Context, id string, grace time.Duration) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if e, ok := m.data[id]; ok {
		e.record.State = StateDemaskedGrace
		e.record.DemaskedAt = float64(time.Now().Unix())
		e.expires = time.Now().Add(grace)
		m.data[id] = e
	}
	return nil
}

// ---- Redis cache --------------------------------------------------------------

type RedisStore struct{ c *redis.Client }

func NewRedisStore(url string) (*RedisStore, error) {
	opt, err := redis.ParseURL(url)
	if err != nil {
		return nil, err
	}
	opt.DialTimeout = 300 * time.Millisecond
	opt.ReadTimeout = 500 * time.Millisecond
	opt.WriteTimeout = 500 * time.Millisecond
	return &RedisStore{c: redis.NewClient(opt)}, nil
}

func (s *RedisStore) Get(ctx context.Context, id string) (*Record, error) {
	raw, err := s.c.Get(ctx, "pii:"+id).Bytes()
	if errors.Is(err, redis.Nil) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var r Record
	if err := json.Unmarshal(raw, &r); err != nil {
		return nil, err
	}
	return &r, nil
}

func (s *RedisStore) SaveIfAbsent(ctx context.Context, id string, r *Record, ttl time.Duration) (bool, error) {
	raw, err := json.Marshal(r)
	if err != nil {
		return false, err
	}
	return s.c.SetNX(ctx, "pii:"+id, raw, ttl).Result()
}

func (s *RedisStore) Set(ctx context.Context, id string, r *Record, ttl time.Duration) error {
	raw, err := json.Marshal(r)
	if err != nil {
		return err
	}
	return s.c.Set(ctx, "pii:"+id, raw, ttl).Err()
}

func (s *RedisStore) MarkDemasked(ctx context.Context, id string, grace time.Duration) error {
	r, err := s.Get(ctx, id)
	if err != nil || r == nil {
		return err
	}
	r.State = StateDemaskedGrace
	r.DemaskedAt = float64(time.Now().Unix())
	return s.Set(ctx, id, r, grace)
}

// ---- Mongo authoritative ------------------------------------------------------

type MongoStore struct {
	col          *mongo.Collection
	indexesReady atomic.Bool
	lastIndexTry atomic.Int64
}

type mongoDoc struct {
	ID        string    `bson:"_id"`
	ExpiresAt time.Time `bson:"expires_at"`
	Record    Record    `bson:"record"`
}

func NewMongoStore(uri, db, col string) (*MongoStore, error) {
	opts := options.Client().ApplyURI(uri).
		SetServerSelectionTimeout(500 * time.Millisecond).
		SetConnectTimeout(500 * time.Millisecond).
		SetSocketTimeout(1500 * time.Millisecond)
	client, err := mongo.Connect(context.Background(), opts)
	if err != nil {
		return nil, err
	}
	return &MongoStore{col: client.Database(db).Collection(col)}, nil
}

func (s *MongoStore) ensureIndexesLazy(ctx context.Context) {
	if s.indexesReady.Load() {
		return
	}
	now := time.Now().Unix()
	last := s.lastIndexTry.Load()
	if now-last < 60 || !s.lastIndexTry.CompareAndSwap(last, now) {
		return
	}
	_, err := s.col.Indexes().CreateOne(ctx, mongo.IndexModel{
		Keys:    bson.D{{Key: "expires_at", Value: 1}},
		Options: options.Index().SetExpireAfterSeconds(0),
	})
	if err == nil {
		s.indexesReady.Store(true)
	}
}

func (s *MongoStore) Get(ctx context.Context, id string) (*Record, error) {
	var doc mongoDoc
	err := s.col.FindOne(ctx, bson.M{"_id": id}).Decode(&doc)
	if errors.Is(err, mongo.ErrNoDocuments) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if !doc.ExpiresAt.IsZero() && time.Now().After(doc.ExpiresAt) {
		return nil, nil
	}
	return &doc.Record, nil
}

func (s *MongoStore) SaveIfAbsent(ctx context.Context, id string, r *Record, ttl time.Duration) (bool, error) {
	s.ensureIndexesLazy(ctx)
	doc := mongoDoc{ID: id, ExpiresAt: time.Now().Add(ttl), Record: *r}
	_, err := s.col.InsertOne(ctx, doc)
	if err == nil {
		return true, nil
	}
	if mongo.IsDuplicateKeyError(err) {
		// reclaim only an expired doc
		res, rerr := s.col.ReplaceOne(ctx,
			bson.M{"_id": id, "expires_at": bson.M{"$lte": time.Now()}}, doc)
		if rerr != nil {
			return false, rerr
		}
		return res.ModifiedCount > 0, nil
	}
	return false, err
}

func (s *MongoStore) MarkDemasked(ctx context.Context, id string, grace time.Duration) error {
	_, err := s.col.UpdateOne(ctx, bson.M{"_id": id}, bson.M{"$set": bson.M{
		"record.state":       StateDemaskedGrace,
		"record.demasked_at": float64(time.Now().Unix()),
		"expires_at":         time.Now().Add(grace),
	}})
	return err
}

// ---- resilient composition ----------------------------------------------------

type breaker struct {
	failures  atomic.Int32
	openedAt  atomic.Int64 // unix nanos, 0 = closed
	threshold int32
	cooldown  time.Duration
}

func (b *breaker) allow() bool {
	if b.failures.Load() < b.threshold {
		return true
	}
	opened := b.openedAt.Load()
	return opened == 0 || time.Since(time.Unix(0, opened)) >= b.cooldown
}

func (b *breaker) success() { b.failures.Store(0); b.openedAt.Store(0) }

func (b *breaker) failure() {
	if b.failures.Add(1) >= b.threshold {
		b.openedAt.Store(time.Now().UnixNano())
	}
}

func (b *breaker) degraded() bool { return b.failures.Load() >= b.threshold }

// ResilientStore: authoritative (Mongo) + optional cache (Redis), same failure
// semantics as the Python ResilientMappingStore.
type ResilientStore struct {
	auth        Store
	cache       *RedisStore
	authBr      breaker
	cacheBr     breaker
	backfillTTL time.Duration
}

func NewResilientStore(auth Store, cache *RedisStore, backfillTTL time.Duration) *ResilientStore {
	rs := &ResilientStore{auth: auth, cache: cache, backfillTTL: backfillTTL}
	rs.authBr = breaker{threshold: 3, cooldown: 10 * time.Second}
	rs.cacheBr = breaker{threshold: 3, cooldown: 10 * time.Second}
	return rs
}

func (r *ResilientStore) AuthDegraded() bool  { return r.authBr.degraded() }
func (r *ResilientStore) CacheDegraded() bool { return r.cache != nil && r.cacheBr.degraded() }

func (r *ResilientStore) Get(ctx context.Context, id string) (*Record, error) {
	cacheOK := false
	if r.cache != nil && r.cacheBr.allow() {
		rec, err := r.cache.Get(ctx, id)
		if err == nil {
			r.cacheBr.success()
			cacheOK = true
			if rec != nil {
				return rec, nil
			}
		} else {
			r.cacheBr.failure()
		}
	}
	if !r.authBr.allow() {
		return nil, ErrStorageUnavailable
	}
	rec, err := r.auth.Get(ctx, id)
	if err != nil {
		r.authBr.failure()
		return nil, ErrStorageUnavailable
	}
	r.authBr.success()
	if rec != nil && cacheOK {
		// best-effort backfill: a cache write failure must not fail the read
		if err := r.cache.Set(ctx, id, rec, r.backfillTTL); err != nil {
			r.cacheBr.failure()
		}
	}
	return rec, nil
}

func (r *ResilientStore) SaveIfAbsent(ctx context.Context, id string, rec *Record, ttl time.Duration) (bool, error) {
	if !r.authBr.allow() {
		return false, ErrStorageUnavailable
	}
	created, err := r.auth.SaveIfAbsent(ctx, id, rec, ttl)
	if err != nil {
		r.authBr.failure()
		return false, ErrStorageUnavailable
	}
	r.authBr.success()
	if created && r.cache != nil && r.cacheBr.allow() {
		if err := r.cache.Set(ctx, id, rec, ttl); err != nil {
			r.cacheBr.failure()
		} else {
			r.cacheBr.success()
		}
	}
	return created, nil
}

func (r *ResilientStore) MarkDemasked(ctx context.Context, id string, grace time.Duration) error {
	if r.authBr.allow() {
		if err := r.auth.MarkDemasked(ctx, id, grace); err != nil {
			r.authBr.failure()
		} else {
			r.authBr.success()
		}
	}
	if r.cache != nil && r.cacheBr.allow() {
		if err := r.cache.MarkDemasked(ctx, id, grace); err != nil {
			r.cacheBr.failure()
		}
	}
	return nil // never fails the demask response
}
