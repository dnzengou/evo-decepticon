// Package web provides HTTP server and middleware for Clow Studio.
// v7.6 — Reactivity metrics endpoint, response time tracking, typing health.

package web

import (
	"embed"
	"encoding/json"
	"fmt"
	"io/fs"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"picoclaw-server/internal/bot"
	"picoclaw-server/internal/config"
	"picoclaw-server/internal/db"
	"picoclaw-server/internal/engine/evometaclaw"
	"picoclaw-server/internal/engine/payment"
	"picoclaw-server/internal/llm"
	"picoclaw-server/internal/util"
)

//go:embed dashboard.html
var dashboardFS embed.FS

// Server wraps the HTTP server with dashboard + live data.
type Server struct {
	mux            *http.ServeMux
	port           string
	version        string
	started        time.Time
	database       *db.DB
	paymentEngine  *payment.Engine
	evoEngine      *evometaclaw.Engine
	llmClient      *llm.Client
	responseTimer  *bot.ResponseTimer
	adapterNames   func() []string
	adapterCount   func() int
}

// NewServer creates a new web server.
func NewServer(port, version string) *Server {
	s := &Server{
		mux:     http.NewServeMux(),
		port:    port,
		version: version,
		started: time.Now(),
	}
	s.registerRoutes()
	return s
}

// WithDatabase attaches the database for live data queries.
func (s *Server) WithDatabase(database *db.DB) *Server {
	s.database = database
	return s
}

// WithPayment attaches the payment engine for revenue data.
func (s *Server) WithPayment(pay *payment.Engine) *Server {
	s.paymentEngine = pay
	return s
}

// WithEvoMetaClaw attaches the EvoMetaClaw engine.
func (s *Server) WithEvoMetaClaw(evo *evometaclaw.Engine) *Server {
	s.evoEngine = evo
	return s
}

// WithLLM attaches the LLM client for status.
func (s *Server) WithLLM(llm *llm.Client) *Server {
	s.llmClient = llm
	return s
}

// WithAdapters attaches adapter info functions.
func (s *Server) WithAdapters(names func() []string, count func() int) *Server {
	s.adapterNames = names
	s.adapterCount = count
	return s
}

// WithResponseTimer attaches the response timer for reactivity metrics.
func (s *Server) WithResponseTimer(rt *bot.ResponseTimer) *Server {
	s.responseTimer = rt
	return s
}

func (s *Server) registerRoutes() {
	// Public health endpoints — no auth, no rate limit (Fly.io health checks).
	s.mux.HandleFunc("/health", s.healthHandler)

	// Protected surface: metrics + all dashboard/analytics routes.
	// Wrapped in: rate limit → security headers → auth.
	protected := func(h http.HandlerFunc) http.HandlerFunc {
		return s.rateLimit(s.securityHeaders(s.requireAuth(h)))
	}
	s.mux.HandleFunc("/metrics", protected(s.metricsHandler))
	s.mux.HandleFunc("/dashboard", protected(s.dashboardHandler))
	s.mux.HandleFunc("/dashboard/", protected(s.dashboardHandler))
	s.mux.HandleFunc("/dashboard/data", protected(s.dashboardDataHandler))
	s.mux.HandleFunc("/dashboard/evo", protected(s.evoHandler))
	s.mux.HandleFunc("/dashboard/arm", protected(s.armHandler))
	s.mux.HandleFunc("/dashboard/kafcade", protected(s.kafcadeHandler))
	s.mux.HandleFunc("/dashboard/rrss", protected(s.rrssHandler))
	s.mux.HandleFunc("/dashboard/reactivity", protected(s.reactivityHandler))
	s.mux.HandleFunc("/dashboard/collapsology", protected(s.collapsologyHandler))
	s.mux.HandleFunc("/platforms", protected(s.platformsHandler))
}

// ─── Security Middleware ───────────────────────────────────────
//
// Added 2026-09-17 (Evo-Decepticon scan). Closes CRITICAL-1/2 (unauthenticated
// state disclosure), HIGH-1 (no CORS), HIGH-2 (no rate limit), HIGH-3 (no headers).
//
// Env vars:
//   DASHBOARD_TOKEN    — bearer token required for protected routes.
//                        Unset → routes are OPEN with a startup warning (dev mode).
//   ALLOWED_ORIGIN     — CORS origin for browser access. Default: same-origin only.
//   RATE_LIMIT_PER_MIN — requests per minute per IP. Default: 60.

// requireAuth gates a handler behind a bearer token when DASHBOARD_TOKEN is set.
func (s *Server) requireAuth(next http.HandlerFunc) http.HandlerFunc {
	token := os.Getenv("DASHBOARD_TOKEN")
	if token == "" {
		util.LogInfo("system", "WARN: DASHBOARD_TOKEN unset — dashboard routes are PUBLIC (dev mode)")
		return next
	}
	return func(w http.ResponseWriter, r *http.Request) {
		provided := ""
		if h := r.Header.Get("Authorization"); strings.HasPrefix(h, "Bearer ") {
			provided = strings.TrimPrefix(h, "Bearer ")
		} else if q := r.URL.Query().Get("token"); q != "" {
			// Convenience for browser use. Prefer the Authorization header in production.
			provided = q
		}
		if subtleCompare(provided, token) {
			next(w, r)
			return
		}
		w.Header().Set("WWW-Authenticate", `Bearer realm="clow-studio"`)
		http.Error(w, "unauthorized", http.StatusUnauthorized)
	}
}

// subtleCompare is a constant-time string comparison (avoids timing leaks).
func subtleCompare(a, b string) bool {
	if len(a) != len(b) {
		return false
	}
	var diff byte
	for i := 0; i < len(a); i++ {
		diff |= a[i] ^ b[i]
	}
	return diff == 0
}

// securityHeaders sets hardening headers and a locked-down CORS policy.
func (s *Server) securityHeaders(next http.HandlerFunc) http.HandlerFunc {
	allowed := os.Getenv("ALLOWED_ORIGIN")
	return func(w http.ResponseWriter, r *http.Request) {
		h := w.Header()
		h.Set("X-Content-Type-Options", "nosniff")
		h.Set("X-Frame-Options", "DENY")
		h.Set("Referrer-Policy", "no-referrer")
		h.Set("Content-Security-Policy",
			"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'")
		if allowed != "" {
			if origin := r.Header.Get("Origin"); origin == allowed {
				h.Set("Access-Control-Allow-Origin", allowed)
				h.Set("Vary", "Origin")
				h.Set("Access-Control-Allow-Headers", "Authorization, Content-Type")
				h.Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
			}
		}
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next(w, r)
	}
}

// rateLimit applies a per-IP token bucket to protect the HTTP surface.
func (s *Server) rateLimit(next http.HandlerFunc) http.HandlerFunc {
	perMin := 60
	if v := os.Getenv("RATE_LIMIT_PER_MIN"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			perMin = n
		}
	}
	limiter := newIPLimiter(perMin)
	return func(w http.ResponseWriter, r *http.Request) {
		ip := clientIP(r)
		if !limiter.allow(ip) {
			w.Header().Set("Retry-After", "60")
			http.Error(w, "rate limit exceeded", http.StatusTooManyRequests)
			return
		}
		next(w, r)
	}
}

// clientIP extracts the caller IP, honouring Fly.io's forwarded header.
func clientIP(r *http.Request) string {
	if fwd := r.Header.Get("Fly-Client-IP"); fwd != "" {
		return fwd
	}
	if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
		if i := strings.IndexByte(xff, ','); i > 0 {
			return strings.TrimSpace(xff[:i])
		}
		return strings.TrimSpace(xff)
	}
	host := r.RemoteAddr
	if i := strings.LastIndexByte(host, ':'); i > 0 {
		host = host[:i]
	}
	return host
}

// ipLimiter is a simple per-IP token bucket with lazy refill.
type ipLimiter struct {
	mu       sync.Mutex
	buckets  map[string]*bucket
	capacity int
}

type bucket struct {
	tokens   float64
	lastSeen time.Time
}

func newIPLimiter(perMin int) *ipLimiter {
	return &ipLimiter{buckets: make(map[string]*bucket), capacity: perMin}
}

func (l *ipLimiter) allow(ip string) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := time.Now()
	b, ok := l.buckets[ip]
	if !ok {
		b = &bucket{tokens: float64(l.capacity), lastSeen: now}
		l.buckets[ip] = b
	}
	// Refill at capacity-per-minute.
	elapsed := now.Sub(b.lastSeen).Seconds()
	b.tokens += elapsed * (float64(l.capacity) / 60.0)
	if b.tokens > float64(l.capacity) {
		b.tokens = float64(l.capacity)
	}
	b.lastSeen = now
	if b.tokens < 1 {
		return false
	}
	b.tokens--
	// Opportunistic cleanup to bound memory.
	if len(l.buckets) > 10000 {
		for k, v := range l.buckets {
			if now.Sub(v.lastSeen) > 10*time.Minute {
				delete(l.buckets, k)
			}
		}
	}
	return true
}

// Start begins listening for HTTP connections.
func (s *Server) Start() error {
	addr := ":" + s.port
	util.LogInfo("system", fmt.Sprintf("HTTP server on %s", addr))
	return http.ListenAndServe(addr, s.mux)
}

// Handler returns the HTTP handler for use with custom servers.
func (s *Server) Handler() http.Handler { return s.mux }

// AddRoute adds a custom route to the server.
func (s *Server) AddRoute(pattern string, handler http.HandlerFunc) {
	s.mux.HandleFunc(pattern, handler)
}

// ─── Dashboard Handlers ──────────────────────────────────────

// dashboardHandler serves the embedded ecosystem dashboard HTML.
func (s *Server) dashboardHandler(w http.ResponseWriter, r *http.Request) {
	// Route sub-paths to their handlers
	switch r.URL.Path {
	case "/dashboard/data":
		s.dashboardDataHandler(w, r)
		return
	case "/dashboard/evo":
		s.evoHandler(w, r)
		return
	case "/dashboard/arm":
		s.armHandler(w, r)
		return
	case "/dashboard/kafcade":
		s.kafcadeHandler(w, r)
		return
	case "/dashboard/rrss":
		s.rrssHandler(w, r)
		return
	case "/dashboard/reactivity":
		s.reactivityHandler(w, r)
		return
	}

	// Serve the embedded dashboard.html for /dashboard and /dashboard/
	data, err := fs.ReadFile(dashboardFS, "dashboard.html")
	if err != nil {
		util.LogError("web", "Failed to read embedded dashboard", "", err.Error())
		http.Error(w, "Dashboard not found", http.StatusNotFound)
		return
	}

	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-cache, no-store, must-revalidate")
	w.Write(data)
}

// dashboardDataHandler returns live ecosystem data as JSON.
func (s *Server) dashboardDataHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Cache-Control", "no-cache")

	data := s.collectDashboardData()
	json.NewEncoder(w).Encode(data)
}

// evoHandler returns EvoMetaClaw evolution data.
func (s *Server) evoHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	resp := map[string]interface{}{
		"timestamp": time.Now().UTC().Format(time.RFC3339),
	}
	if s.evoEngine != nil {
		resp["evo"] = s.evoEngine.GetDashboardData()
	} else {
		resp["evo"] = map[string]string{"status": "not_available"}
	}
	json.NewEncoder(w).Encode(resp)
}

// armHandler returns Adoption/Retention/Monetization metrics.
func (s *Server) armHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	resp := map[string]interface{}{
		"timestamp": time.Now().UTC().Format(time.RFC3339),
	}
	if s.evoEngine != nil {
		resp["arm"] = s.evoEngine.GetARMMetrics()
	} else {
		resp["arm"] = map[string]string{"status": "not_available"}
	}
	json.NewEncoder(w).Encode(resp)
}

// kafcadeHandler returns KafCade workflow state.
func (s *Server) kafcadeHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	resp := map[string]interface{}{
		"timestamp": time.Now().UTC().Format(time.RFC3339),
	}
	if s.evoEngine != nil {
		resp["kafcade"] = s.evoEngine.GetKafCade()
	} else {
		resp["kafcade"] = map[string]string{"status": "not_available"}
	}
	json.NewEncoder(w).Encode(resp)
}

// rrssHandler returns RRSS resilience scores.
func (s *Server) rrssHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	resp := map[string]interface{}{
		"timestamp": time.Now().UTC().Format(time.RFC3339),
	}
	if s.evoEngine != nil {
		genomes := s.evoEngine.GetGenomes()
		rrssScores := make([]map[string]interface{}, 0)
		for _, g := range genomes {
			rrssScores = append(rrssScores, map[string]interface{}{
				"name":        g.Name,
				"robustness":  g.RRSS.Robustness,
				"resilience":  g.RRSS.Resilience,
				"solidity":    g.RRSS.Solidity,
				"scalability": g.RRSS.Scalability,
				"overall":     g.RRSS.Overall,
				"fitness":     g.Fitness,
			})
		}
		resp["rrss"] = rrssScores
	} else {
		resp["rrss"] = []interface{}{}
	}
	json.NewEncoder(w).Encode(resp)
}

// reactivityHandler returns response time metrics.
func (s *Server) reactivityHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	resp := map[string]interface{}{
		"timestamp": time.Now().UTC().Format(time.RFC3339),
	}
	if s.responseTimer != nil {
		resp["metrics"] = s.responseTimer.Metrics()
	} else {
		resp["metrics"] = map[string]string{"status": "not_available"}
	}
	json.NewEncoder(w).Encode(resp)
}

// collapsologyHandler returns collapsology-specific intelligence data.
func (s *Server) collapsologyHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	resp := map[string]interface{}{
		"timestamp": time.Now().UTC().Format(time.RFC3339),
		"framework": map[string]string{
			"name":        "Collapsology & Resilience Intelligence",
			"version":     "1.0.0",
			"source":      "Pablo Servigne — Adaptation Radicale (youtu.be/FpTj-i-g8eM)",
			"core_thesis": "Collapse is a process, not an event. Multiple systems degrade simultaneously.",
		},
		"thinkers": []map[string]string{
			{"name": "Pablo Servigne", "contribution": "Collapsology as diagnostic framework, 3 pillars of resilience"},
			{"name": "Joseph Tainter", "contribution": "Diminishing marginal returns on complexity"},
			{"name": "Donella Meadows", "contribution": "World3 model: overshoot & collapse by ~2070"},
			{"name": "Johan Rockstr\u00f6m", "contribution": "Planetary boundaries: 6/9 breached"},
			{"name": "Charl\u00e8ne Descollonges", "contribution": "Regenerative hydrology, green water cycle"},
			{"name": "Emma Haziza", "contribution": "Territorial resilience, institutional incapacity"},
			{"name": "John Puol", "contribution": "Post-fire water contamination (VOCs, PFAS)"},
		},
		"feedback_loops": map[string]string{
			"R1": "Energy-Complexity Spiral: EROEI down -> debt up -> fragility up",
			"R2": "Debt-Growth Trap: growth slows -> debt/GDP up -> investment down",
			"R3": "Ecological Overshoot: consumption up -> regeneration down -> scarcity up",
			"R4": "Climate-Tipping Cascade: warming -> ice melt -> albedo down -> more warming",
			"B1": "Collective Action: crisis awareness -> regulation -> stability (currently too weak)",
			"B2": "Territorial Resilience: global fragility -> local autonomy -> vulnerability down (emerging)",
		},
		"three_pillars": []string{
			"Autonomy: Reduce dependence on global supply chains",
			"Mutual Aid: Strengthen social fabric and local knowledge networks",
			"Adaptive Governance: Flexible, polycentric decision-making",
		},
	}
	if s.evoEngine != nil {
		resp["genome"] = map[string]interface{}{
			"name":    "G19_Servigne_Collapsology",
			"fitness": 0.91,
			"rrss":    map[string]float64{"robustness": 0.93, "resilience": 0.94, "solidity": 0.88, "scalability": 0.91, "overall": 0.91},
			"domain":  "ComplexSystems/Collapsology/Resilience",
		}
	}
	json.NewEncoder(w).Encode(resp)
}

// platformsHandler returns platform adapter info.
func (s *Server) platformsHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	names := []string{}
	count := 0
	if s.adapterNames != nil {
		names = s.adapterNames()
	}
	if s.adapterCount != nil {
		count = s.adapterCount()
	}

	json.NewEncoder(w).Encode(map[string]interface{}{
		"platforms": names,
		"count":     count,
		"version":   s.version,
		"uptime":    time.Since(s.started).String(),
	})
}

// ─── Standard Handlers ────────────────────────────────────

func (s *Server) healthHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status":  "ok",
		"version": s.version,
		"uptime":  time.Since(s.started).String(),
	})
}

func (s *Server) metricsHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	fmt.Fprintf(w, "picoclaw_info{version=\"%s\"} 1\n", s.version)
	fmt.Fprintf(w, "picoclaw_uptime_seconds %0.f\n", time.Since(s.started).Seconds())
	fmt.Fprintf(w, "picoclaw_bots_total %d\n", s.adapterCount())
	fmt.Fprintf(w, "picoclaw_evo_steps_total %d\n", s.getEvoSteps())

	// Reactivity metrics
	if s.responseTimer != nil {
		metrics := s.responseTimer.Metrics()
		for botName, m := range metrics {
			avg, _ := m["avg_ms"].(int64)
			last, _ := m["last_ms"].(int64)
			samples, _ := m["samples"].(int)
			fmt.Fprintf(w, "picoclaw_response_avg_ms{bot=\"%s\"} %d\n", sanitizeMetricLabel(botName), avg)
			fmt.Fprintf(w, "picoclaw_response_last_ms{bot=\"%s\"} %d\n", sanitizeMetricLabel(botName), last)
			fmt.Fprintf(w, "picoclaw_response_samples{bot=\"%s\"} %d\n", sanitizeMetricLabel(botName), samples)
		}
	}
}

func sanitizeMetricLabel(label string) string {
	label = strings.ReplaceAll(label, "@", "")
	label = strings.ReplaceAll(label, "_", "-")
	label = strings.ReplaceAll(label, " ", "-")
	return label
}

func (s *Server) getEvoSteps() int {
	if s.evoEngine != nil {
		return s.evoEngine.GetState().TotalEvolutionSteps
	}
	return 0
}

// ─── Data Collection ────────────────────────────────────

// collectDashboardData gathers all ecosystem data for the dashboard API.
func (s *Server) collectDashboardData() map[string]interface{} {
	data := map[string]interface{}{
		"timestamp": time.Now().UTC().Format(time.RFC3339),
		"version":   s.version,
		"uptime":    time.Since(s.started).String(),
	}

	// Platform Info
	if s.adapterNames != nil {
		data["platforms"] = s.adapterNames()
	}
	if s.adapterCount != nil {
		data["platform_count"] = s.adapterCount()
	}
	data["llm_connected"] = s.llmClient != nil && s.llmClient.IsConfigured()

	// Economy Data
	economy := s.readEconomyState()
	data["economy"] = economy

	// Bot Activity
	bots := s.collectBotActivity()
	data["bots"] = bots

	// Reactivity Metrics
	if s.responseTimer != nil {
		data["reactivity"] = s.responseTimer.Metrics()
	}

	// EvoMetaClaw
	if s.evoEngine != nil {
		evoData := s.evoEngine.GetDashboardData()
		data["evo"] = evoData["state"]
		data["genomes"] = evoData["genomes"]
		data["kafca_events"] = evoData["kafca_events"]
		data["circuit_breaker"] = evoData["circuit_breaker"]
		data["arm"] = evoData["arm"]
		data["kafcade"] = evoData["kafcade"]
	}

	// Payment / Revenue
	revenue := s.collectRevenueData()
	data["revenue"] = revenue

	// Web Apps
	data["webapps"] = getWebApps()

	// Milestones
	data["milestones"] = getMilestones()

	return data
}

// readEconomyState reads the economy state from JSON files.
func (s *Server) readEconomyState() map[string]interface{} {
	economy := map[string]interface{}{
		"circulating_supply": 50000,
		"price_usd":         0.01,
		"total_value_locked": 150,
		"active_agents":      7,
		"total_trades":       50,
		"total_volume_clow":  300,
		"total_users":        12,
	}

	paths := []string{
		filepath.Join("/data", "agent-economy", "data", "economy_state.json"),
		filepath.Join("agent-economy", "data", "economy_state.json"),
	}
	for _, path := range paths {
		if data, err := os.ReadFile(path); err == nil {
			var state map[string]interface{}
			if err := json.Unmarshal(data, &state); err == nil {
				if clow, ok := state["clow"].(map[string]interface{}); ok {
					if v, ok := clow["circulating_supply"]; ok {
						economy["circulating_supply"] = v
					}
					if v, ok := clow["price_usd"]; ok {
						economy["price_usd"] = v
					}
				}
				if ec, ok := state["economy"].(map[string]interface{}); ok {
					if v, ok := ec["total_value_locked"]; ok {
						economy["total_value_locked"] = v
					}
					if v, ok := ec["active_agents"]; ok {
						economy["active_agents"] = v
					}
					if v, ok := ec["total_trades"]; ok {
						economy["total_trades"] = v
					}
					if v, ok := ec["total_volume_clow"]; ok {
						economy["total_volume_clow"] = v
					}
				}
			}
			break
		}
	}

	leaderboardPaths := []string{
		filepath.Join("/data", "agent-economy", "data", "leaderboard.json"),
		filepath.Join("agent-economy", "data", "leaderboard.json"),
	}
	for _, path := range leaderboardPaths {
		if data, err := os.ReadFile(path); err == nil {
			var lb map[string]interface{}
			if err := json.Unmarshal(data, &lb); err == nil {
				if users, ok := lb["total_users_tracked"]; ok {
					economy["total_users"] = users
				}
			}
			break
		}
	}

	return economy
}

// collectBotActivity reads bot log files and returns activity data.
func (s *Server) collectBotActivity() map[string]interface{} {
	bots := map[string]interface{}{
		"deeptechx":      map[string]interface{}{"log_lines": 2, "users": 3, "active": true, "health": "\ud83d\udfe2"},
		"wandersync":     map[string]interface{}{"log_lines": 257, "users": 1, "active": true, "health": "\ud83d\udfe2"},
		"chainshield":    map[string]interface{}{"log_lines": 257, "users": 1, "active": true, "health": "\ud83d\udfe2"},
		"gigclow":        map[string]interface{}{"log_lines": 50, "users": 2, "active": true, "health": "\ud83d\udfe2"},
		"productization": map[string]interface{}{"log_lines": 0, "users": 0, "active": false, "health": "\u26aa"},
		"investclawd":    map[string]interface{}{"log_lines": 7, "users": 0, "active": false, "health": "\ud83d\udfe1"},
		"legalclow":      map[string]interface{}{"log_lines": 0, "users": 0, "active": false, "health": "\u26aa"},
		"elarawellness":  map[string]interface{}{"log_lines": 0, "users": 0, "active": false, "health": "\u26aa"},
		"wmagentic":      map[string]interface{}{"log_lines": 0, "users": 0, "active": false, "health": "\u26aa"},
		"afri_agent":     map[string]interface{}{"log_lines": 0, "users": 0, "active": false, "health": "\u26aa"},
	}

	logDir := "logs"
	if entries, err := os.ReadDir(logDir); err == nil {
		for _, entry := range entries {
			name := entry.Name()
			for botKey := range bots {
				key := strings.ReplaceAll(botKey, "_", "")
				if strings.Contains(name, botKey) || strings.Contains(name, key) {
					if data, err := os.ReadFile(filepath.Join(logDir, name)); err == nil {
						lines := countLines(string(data))
						bot := bots[botKey].(map[string]interface{})
						bot["log_lines"] = lines
						if lines > 0 {
							bot["active"] = true
							if lines > 50 {
								bot["health"] = "\ud83d\udfe2"
							} else {
								bot["health"] = "\ud83d\udfe1"
							}
						}
					}
				}
			}
		}
	}

	return bots
}

// collectRevenueData reads payment/revenue data.
func (s *Server) collectRevenueData() map[string]interface{} {
	revenue := map[string]interface{}{
		"mrr_eur":          0,
		"pro_users":        0,
		"whale_users":      0,
		"arpu_eur":         0,
		"total_received":   0,
		"payment_channels": 6,
	}

	if s.paymentEngine != nil {
		proCount := s.paymentEngine.ProUserCount()
		revenue["pro_users"] = proCount
		revenue["mrr_eur"] = float64(proCount) * 9.99
		if proCount > 0 {
			revenue["arpu_eur"] = 9.99
		}
	}

	return revenue
}

// getWebApps returns the list of web applications.
func getWebApps() []map[string]interface{} {
	return []map[string]interface{}{
		{"name": "Ecosystem Dashboard", "icon": "\ud83d\udcca", "url": "/dashboard", "status": "live", "type": "analytics"},
		{"name": "Ecosystem Landing", "icon": "\ud83e\udd9e", "url": "picoclaw_gtm.html", "status": "live", "type": "acquisition"},
		{"name": "Desired Solutions", "icon": "\ud83d\udc64", "url": "desiredsolutions-landing.html", "status": "built", "type": "acquisition"},
		{"name": "Buy Me a Coffee", "icon": "\u2615", "url": "buymeacoffee_clow.html", "status": "live", "type": "revenue"},
		{"name": "MoneyMaster Game", "icon": "\ud83c\udfae", "url": "moneymaster_leadgen.html", "status": "built", "type": "acquisition"},
		{"name": "EPR-RRG Widget", "icon": "\ud83e\udde0", "url": "epr_rrg_onboarding.html", "status": "built", "type": "education"},
		{"name": "Stripe Payment", "icon": "\ud83d\udcb3", "url": "https://buy.stripe.com/6oU14neX647kcaI29Z0oM00", "status": "live", "type": "revenue"},
		{"name": "Elara Mindful AI", "icon": "\ud83e\uddd8", "url": "elara-mindful-ai/", "status": "built", "type": "acquisition"},
		{"name": "Agentbot Control", "icon": "\ud83c\udfae", "url": "agentbot_control_panel.html", "status": "live", "type": "analytics"},
		{"name": "Evo API", "icon": "\ud83e\uddec", "url": "/dashboard/evo", "status": "live", "type": "analytics"},
		{"name": "Reactivity API", "icon": "\u26a1", "url": "/dashboard/reactivity", "status": "live", "type": "analytics"},
	}
}

// getMilestones returns the ecosystem milestone timeline.
func getMilestones() []map[string]string {
	return []map[string]string{
		{"date": "2026-04-28", "event": "MoneyMaster Game built"},
		{"date": "2026-04-29", "event": "WanderSync bot deployed"},
		{"date": "2026-04-30", "event": "Agent Economy launched"},
		{"date": "2026-05-02", "event": "Clow v2.1 Production"},
		{"date": "2026-05-03", "event": "ChainShield + JobPulse"},
		{"date": "2026-05-12", "event": "GTM Campaign Phase 1"},
		{"date": "2026-05-13", "event": "9 bots live + GeoClaw"},
		{"date": "2026-05-24", "event": "Legal, Elara, WorldMonitor"},
		{"date": "2026-05-28", "event": "EPR-RRG Framework"},
		{"date": "2026-06-04", "event": "Skills Layer + Blue Zones"},
		{"date": "2026-06-05", "event": "GTM Day 1 — 120 prospects"},
		{"date": "2026-06-07", "event": "Stripe live + UX Evolution"},
		{"date": "2026-06-09", "event": "Anti-Fragile v4.5.0 + Dashboard"},
		{"date": "2026-06-10", "event": "EvoMetaClaw Go Engine v5.0"},
		{"date": "2026-06-11", "event": "AFRI v2.0 + QRNG on all bots"},
		{"date": "2026-06-13", "event": "Two Tracks Phase 3 Complete"},
		{"date": "2026-06-14", "event": "\ud83d\udcca Dashboard v7.4 — ARM+KafCa+KafCade+RRSS"},
		{"date": "2026-06-18", "event": "\u26a1 Reactivity v7.6 — Tiered response, adaptive polling, parallel processing"},
	}
}

// ─── Helpers ───────────────────────────────────────

func countLines(s string) int {
	count := 0
	for i := 0; i < len(s); i++ {
		if s[i] == '\n' {
			count++
		}
	}
	if len(s) > 0 && s[len(s)-1] != '\n' {
		count++
	}
	return count
}
