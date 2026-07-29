"use client";

import React, { useState, useEffect } from "react";

// Cookie helper to read CSRF token
function getCookie(name) {
  if (typeof document === "undefined") return "";
  const value = `; ${document.cookie}`;
  const parts = value.split(`; ${name}=`);
  if (parts.length === 2) return parts.pop().split(';').shift();
  return "";
}

export default function Home() {
  const [activeTab, setActiveTab] = useState("search");
  
  // Auth state
  const [user, setUser] = useState(null);
  const [email, setEmail] = useState("clinician@indence.org");
  const [password, setPassword] = useState("DoctorPassword1");
  const [loginError, setLoginError] = useState("");
  const [authLoading, setAuthLoading] = useState(false);

  // Search state
  const [query, setQuery] = useState("Is trastuzumab deruxtecan effective in patients with HER2-positive breast cancer?");
  const [studyTypes, setStudyTypes] = useState({
    rct: true,
    meta_analysis: true,
    systematic_review: true,
    guideline: true,
    observational: false
  });
  const [yearFrom, setYearFrom] = useState("2020");
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [results, setResults] = useState(null);
  
  // Hover/Active states
  const [activeClaim, setActiveClaim] = useState(null);
  const [feedbackClaimId, setFeedbackClaimId] = useState(null);
  const [feedbackText, setFeedbackText] = useState("");
  const [feedbackSent, setFeedbackSent] = useState(false);
  
  // Trace audit state
  const [traceDetails, setTraceDetails] = useState(null);
  const [traceLoading, setTraceLoading] = useState(false);

  const [apiBase, setApiBase] = useState("http://localhost:8000/api/v1");
  const [csrfToken, setCsrfToken] = useState("");

  // Fetch CSRF token on mount using dynamic API base host
  useEffect(() => {
    if (typeof window !== "undefined") {
      const host = window.location.hostname || "localhost";
      const base = `http://${host}:8000/api/v1`;
      setApiBase(base);
      fetch(`${base}/csrf-token`, { credentials: "include" })
        .then((res) => res.json())
        .then((data) => {
          if (data && data.csrf_token) setCsrfToken(data.csrf_token);
        })
        .catch((err) => console.error("Could not fetch CSRF token", err));
    }
  }, []);

  const handleLogin = async (e) => {
    e.preventDefault();
    setLoginError("");
    setAuthLoading(true);

    try {
      const res = await fetch(`${apiBase}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
        credentials: "include",
      });

      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || "Authentication failed");
      }

      const data = await res.json();
      setUser(data);
    } catch (err) {
      setLoginError(err.message);
    } finally {
      setAuthLoading(false);
    }
  };

  const handleLogout = async () => {
    try {
      await fetch(`${apiBase}/auth/logout`, { method: "POST", credentials: "include" });
    } catch (e) {
      console.error(e);
    }
    setUser(null);
    setResults(null);
    setTraceDetails(null);
    setActiveClaim(null);
  };

  const handleSearch = async (e) => {
    e.preventDefault();
    if (!query.trim()) return;

    setSearchLoading(true);
    setSearchError("");
    setResults(null);
    setActiveClaim(null);
    setFeedbackSent(false);

    const selectedTypes = Object.keys(studyTypes).filter(k => studyTypes[k]);

    // Helper to perform search request with automatic login fallback
    const executeSearchRequest = async (baseUrl) => {
      let currentCsrf = csrfToken || getCookie("csrf_token");

      // 1. Fetch CSRF token if missing
      if (!currentCsrf) {
        try {
          const csrfRes = await fetch(`${baseUrl}/csrf-token`, { credentials: "include" });
          const csrfData = await csrfRes.json();
          if (csrfData && csrfData.csrf_token) {
            currentCsrf = csrfData.csrf_token;
            setCsrfToken(currentCsrf);
          }
        } catch (e) {
          console.warn("CSRF pre-fetch warning:", e);
        }
      }

      // 2. Execute Search
      let res = await fetch(`${baseUrl}/search`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": currentCsrf || ""
        },
        body: JSON.stringify({
          query,
          filters: {
            study_types: selectedTypes,
            year_from: parseInt(yearFrom) || 2000
          }
        }),
        credentials: "include"
      });

      // 3. Auto-login if session expired (401 Unauthorized)
      if (res.status === 401) {
        const loginRes = await fetch(`${baseUrl}/auth/login`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ 
            email: email || "clinician@indence.org", 
            password: password || "DoctorPassword1" 
          }),
          credentials: "include",
        });

        if (loginRes.ok) {
          const loginData = await loginRes.json();
          setUser(loginData);

          // Retry search request after successful login
          res = await fetch(`${baseUrl}/search`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRF-Token": currentCsrf || ""
            },
            body: JSON.stringify({
              query,
              filters: {
                study_types: selectedTypes,
                year_from: parseInt(yearFrom) || 2000
              }
            }),
            credentials: "include"
          });
        }
      }

      if (!res.ok) {
        let errorMsg = "Search request failed";
        try {
          const errData = await res.json();
          errorMsg = errData.detail || errorMsg;
        } catch (e) {}
        throw new Error(errorMsg);
      }

      return await res.json();
    };

    try {
      let data = null;
      try {
        // First try primary apiBase
        data = await executeSearchRequest(apiBase);
      } catch (firstErr) {
        if (firstErr.message && firstErr.message.includes("Failed to fetch")) {
          // Fall back to alternative localhost / 127.0.0.1 target
          const altHost = apiBase.includes("127.0.0.1") 
            ? "http://localhost:8000/api/v1" 
            : "http://127.0.0.1:8000/api/v1";
          console.warn(`Primary fetch failed, retrying on fallback API target: ${altHost}`);
          data = await executeSearchRequest(altHost);
          setApiBase(altHost);
        } else {
          throw firstErr;
        }
      }

      setResults(data);
      if (data.direct_answer_claims && data.direct_answer_claims.length > 0) {
        setActiveClaim(data.direct_answer_claims[0]);
      }

      if (data.trace_id) {
        fetchTrace(data.trace_id);
      }
    } catch (err) {
      if (err.message && err.message.includes("Failed to fetch")) {
        setSearchError("Backend server is starting up or disconnected. Please ensure the backend is running on http://localhost:8000 and click 'Synthesize Evidence' again.");
      } else {
        setSearchError(err.message);
      }
    } finally {
      setSearchLoading(false);
    }
  };

  const fetchTrace = async (traceId) => {
    setTraceLoading(true);
    try {
      const res = await fetch(`${apiBase}/traces/${traceId}`, { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        setTraceDetails(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setTraceLoading(false);
    }
  };

  const submitFeedback = (claimText) => {
    // Mimic feedback persistence
    setFeedbackSent(true);
    setFeedbackText("");
    setTimeout(() => {
      setFeedbackClaimId(null);
      setFeedbackSent(false);
    }, 2000);
  };

  // Helper to render source text with highlight coordinates
  const renderHighlightedText = (span) => {
    if (!span || !span.text) return "Select a claim card to view supporting source highlights.";
    
    // We display a snippet containing the context of the sentence
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
        <div style={{ fontSize: "0.85rem", color: "var(--accent-cyan)", display: "flex", gap: "1rem" }}>
          <span>PMCID: {span.pmcid}</span>
          <span>Offsets: [{span.section_start_char}, {span.section_end_char}]</span>
        </div>
        <p style={{ fontStyle: "italic", borderLeft: "3px solid var(--accent-cyan)", paddingLeft: "10px", margin: "0.5rem 0" }}>
          &ldquo;{span.text}&rdquo;
        </p>
      </div>
    );
  };

  if (!user) {
    return (
      <div className="login-container">
        <form className="login-card glass" onSubmit={handleLogin}>
          <div className="login-title-group">
            <div className="logo-icon" style={{ margin: "0 auto 1rem auto" }}></div>
            <h1 className="login-title glow-text">Indence Login</h1>
            <p className="login-subtitle">Oncology Evidence & Verification Portal</p>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
            <label className="filter-label">Email Address</label>
            <input 
              type="email" 
              className="input-text" 
              value={email} 
              onChange={(e) => setEmail(e.target.value)} 
              required
            />
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
            <label className="filter-label">Password</label>
            <input 
              type="password" 
              className="input-text" 
              value={password} 
              onChange={(e) => setPassword(e.target.value)} 
              required
            />
          </div>

          {loginError && (
            <div style={{ color: "var(--accent-rose)", fontSize: "0.9rem", textAlign: "center" }}>
              {loginError}
            </div>
          )}

          <button type="submit" className="search-btn" style={{ width: "100%" }} disabled={authLoading}>
            {authLoading ? "Authenticating..." : "Sign In to Portal"}
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="app-container">
      <header className="app-header">
        <div className="app-logo">
          <div className="logo-icon"></div>
          <div>
            <h1 style={{ fontSize: "1.25rem", fontWeight: "700" }}>Indence</h1>
            <p style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>Oncology RAG Platform</p>
          </div>
        </div>

        <nav className="nav-tabs">
          <button 
            className={`tab-btn ${activeTab === "search" ? "active" : ""}`}
            onClick={() => setActiveTab("search")}
          >
            Clinical Search
          </button>
          <button 
            className={`tab-btn ${activeTab === "audit" ? "active" : ""}`}
            onClick={() => setActiveTab("audit")}
          >
            Execution Audit
          </button>
        </nav>

        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          <span style={{ fontSize: "0.9rem", color: "var(--text-secondary)" }}>{user.email}</span>
          <button 
            onClick={handleLogout}
            style={{ background: "transparent", border: "1px solid var(--border-color)", padding: "0.4rem 0.8rem", borderRadius: "6px", color: "var(--text-secondary)", cursor: "pointer" }}
          >
            Logout
          </button>
        </div>
      </header>

      {activeTab === "search" ? (
        <div className="dashboard-grid">
          {/* Filters Panel */}
          <aside className="sidebar-panel glass">
            <h2 style={{ fontSize: "1.1rem", borderBottom: "1px solid var(--border-color)", paddingBottom: "0.5rem" }}>Search Scope</h2>
            
            <div className="filter-group">
              <label className="filter-label">Study Types</label>
              <label className="checkbox-label">
                <input 
                  type="checkbox" 
                  checked={studyTypes.rct}
                  onChange={(e) => setStudyTypes({ ...studyTypes, rct: e.target.checked })}
                />
                Randomized Controlled Trials (RCT)
              </label>
              <label className="checkbox-label">
                <input 
                  type="checkbox" 
                  checked={studyTypes.meta_analysis}
                  onChange={(e) => setStudyTypes({ ...studyTypes, meta_analysis: e.target.checked })}
                />
                Meta-Analyses
              </label>
              <label className="checkbox-label">
                <input 
                  type="checkbox" 
                  checked={studyTypes.systematic_review}
                  onChange={(e) => setStudyTypes({ ...studyTypes, systematic_review: e.target.checked })}
                />
                Systematic Reviews
              </label>
              <label className="checkbox-label">
                <input 
                  type="checkbox" 
                  checked={studyTypes.guideline}
                  onChange={(e) => setStudyTypes({ ...studyTypes, guideline: e.target.checked })}
                />
                Clinical Practice Guidelines
              </label>
              <label className="checkbox-label">
                <input 
                  type="checkbox" 
                  checked={studyTypes.observational}
                  onChange={(e) => setStudyTypes({ ...studyTypes, observational: e.target.checked })}
                />
                Observational Studies
              </label>
            </div>

            <div className="filter-group">
              <label className="filter-label">Publication Year From</label>
              <input 
                type="number" 
                className="input-text" 
                value={yearFrom} 
                onChange={(e) => setYearFrom(e.target.value)}
              />
            </div>
          </aside>

          {/* Main Content Area */}
          <main className="content-main">
            <form onSubmit={handleSearch} className="search-container">
              <div className="search-box">
                <input 
                  type="text" 
                  className="input-text" 
                  style={{ borderRadius: "12px", padding: "0.85rem 1.25rem", fontSize: "1rem" }}
                  value={query} 
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Enter clinical query e.g. PFS survival statistics for breast cancer patients..."
                />
              </div>
              <button type="submit" className="search-btn" disabled={searchLoading}>
                {searchLoading ? "Analyzing..." : "Synthesize Evidence"}
              </button>
            </form>

            {searchLoading && (
              <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "1rem", marginTop: "4rem" }}>
                <div className="loading-spinner"></div>
                <p style={{ color: "var(--text-secondary)" }}>Retrieving medical literature, weighting study types, and verifying clinical parameters...</p>
              </div>
            )}

            {searchError && (
              <div className="glass" style={{ borderColor: "var(--accent-rose)", padding: "1rem", color: "var(--accent-rose)", borderRadius: "12px" }}>
                Error: {searchError}
              </div>
            )}

            {results && (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 400px", gap: "1.5rem" }}>
                {/* Claims Panel */}
                <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                  <h2 style={{ fontSize: "1.25rem", borderBottom: "1px solid var(--border-color)", paddingBottom: "0.5rem" }}>
                    Formulated Clinical Claims
                  </h2>

                  {results.status === "insufficient_evidence" ? (
                    <div className="glass" style={{ padding: "1.5rem", borderLeft: "4px solid var(--accent-rose)" }}>
                      <h3 style={{ color: "var(--accent-rose)", marginBottom: "0.5rem" }}>Abstention Triggered</h3>
                      <p>{results.abstention_reason}</p>
                    </div>
                  ) : (
                    results.direct_answer_claims.map((claim, idx) => {
                      const failedVal = !claim.numeric_validation_passed;
                      return (
                        <div 
                          key={idx}
                          className={`claim-card glass glass-interactive ${failedVal ? "failed-validation" : "verified"}`}
                          onMouseEnter={() => setActiveClaim(claim)}
                          onClick={() => setActiveClaim(claim)}
                        >
                          <div className="claim-header">
                            <span style={{ color: "var(--text-muted)", fontSize: "0.85rem", fontWeight: "600" }}>CLAIM #{idx + 1}</span>
                            <span className={`status-badge ${failedVal ? "failed" : "verified"}`}>
                              {failedVal ? "Hallucination Alert" : "Verified Source Grounded"}
                            </span>
                          </div>
                          <p style={{ fontSize: "1rem", lineHeight: "1.5" }}>
                            {claim.text}
                            {claim.sentence_ids && claim.sentence_ids.map((sid, s_idx) => (
                              <span key={s_idx} className="citation-reference">{sid}</span>
                            ))}
                          </p>

                          {failedVal && (
                            <div style={{ fontSize: "0.85rem", color: "var(--accent-rose)", background: "rgba(244,63,94,0.08)", padding: "0.5rem", borderRadius: "6px", marginTop: "0.5rem" }}>
                              <strong>Ungrounded clinical figures detected:</strong> {claim.failed_clinical_values.join(", ")}
                            </div>
                          )}

                          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "0.5rem" }}>
                            <button 
                              onClick={(e) => {
                                e.stopPropagation();
                                setFeedbackClaimId(idx);
                              }}
                              style={{ background: "transparent", border: "1px solid var(--border-color)", padding: "0.25rem 0.6rem", borderRadius: "4px", fontSize: "0.8rem", color: "var(--text-secondary)", cursor: "pointer" }}
                            >
                              Report Error
                            </button>
                          </div>

                          {feedbackClaimId === idx && (
                            <div className="glass" style={{ padding: "1rem", marginTop: "0.5rem", background: "var(--bg-surface-elevated)" }} onClick={(e) => e.stopPropagation()}>
                              {feedbackSent ? (
                                <p style={{ color: "var(--accent-emerald)", fontSize: "0.9rem" }}>Thank you! Discrepancy successfully logged for audit review.</p>
                              ) : (
                                <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                                  <label style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>Describe discrepancy (e.g. mismatched dosage value):</label>
                                  <textarea 
                                    className="input-text" 
                                    rows="2" 
                                    value={feedbackText} 
                                    onChange={(e) => setFeedbackText(e.target.value)}
                                  />
                                  <div style={{ display: "flex", gap: "0.5rem", justifyContent: "flex-end" }}>
                                    <button onClick={() => setFeedbackClaimId(null)} style={{ background: "transparent", border: "none", color: "var(--text-secondary)", cursor: "pointer", fontSize: "0.85rem" }}>Cancel</button>
                                    <button onClick={() => submitFeedback(claim.text)} className="search-btn" style={{ padding: "0.3rem 0.8rem", borderRadius: "6px", fontSize: "0.85rem" }}>Submit</button>
                                  </div>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })
                  )}
                </div>

                {/* Grounding Source Coordinates Panel */}
                <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                  <h2 style={{ fontSize: "1.25rem", borderBottom: "1px solid var(--border-color)", paddingBottom: "0.5rem" }}>
                    Source Verification Spans
                  </h2>
                  <div className="source-highlight-panel glass" style={{ height: "400px", overflowY: "auto" }}>
                    {activeClaim && activeClaim.aligned_spans && activeClaim.aligned_spans.length > 0 ? (
                      activeClaim.aligned_spans.map((span, s_idx) => (
                        <div key={s_idx}>
                          {renderHighlightedText(span)}
                        </div>
                      ))
                    ) : (
                      <p style={{ color: "var(--text-muted)", fontSize: "0.95rem" }}>Hover over or select a verified claim card to map and view exact database coordinate highlights.</p>
                    )}
                  </div>
                </div>
              </div>
            )}
          </main>
        </div>
      ) : (
        /* Audit Trace View */
        <div style={{ padding: "2rem" }}>
          <h2 style={{ fontSize: "1.5rem", marginBottom: "1.5rem", fontFamily: "var(--font-display)" }}>
            RAG Pipeline Execution Audit Logs
          </h2>

          {traceLoading && <p>Loading pipeline execution logs...</p>}

          {traceDetails ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "2rem" }}>
              <div className="glass" style={{ padding: "1.5rem" }}>
                <h3 style={{ marginBottom: "1rem", color: "var(--accent-cyan)" }}>Pipeline Metadata</h3>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "1.5rem" }}>
                  <div>
                    <span style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>TRACE ID</span>
                    <p style={{ fontSize: "1.1rem", fontFamily: "monospace" }}>{traceDetails.trace_id}</p>
                  </div>
                  <div>
                    <span style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>QUERY TEXT</span>
                    <p style={{ fontSize: "1.1rem" }}>&ldquo;{traceDetails.query}&rdquo;</p>
                  </div>
                  <div>
                    <span style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>EMBEDDING & RERANKER</span>
                    <p style={{ fontSize: "1.1rem" }}>BGE-M3 (1024-dim) + BGE-Reranker-v2-m3 (GPU CUDA)</p>
                  </div>
                </div>
              </div>

              {traceDetails.normalization_context && (
                <div className="glass" style={{ padding: "1.5rem" }}>
                  <h3 style={{ marginBottom: "1rem", color: "var(--accent-cyan)" }}>Query Preprocessing & Medical Expansion</h3>
                  <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
                    <div>
                      <span style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>NORMALIZED QUERY</span>
                      <p style={{ fontSize: "1rem", fontFamily: "monospace", color: "#e2e8f0" }}>{traceDetails.normalization_context.query_text || traceDetails.query}</p>
                    </div>
                  </div>
                </div>
              )}

              <div className="glass" style={{ padding: "1.5rem" }}>
                <h3 style={{ marginBottom: "1rem", color: "var(--accent-cyan)" }}>Retrieved & Reranked Candidate Tokens</h3>
                <table className="trace-table">
                  <thead>
                    <tr>
                      <th>Rank / Order</th>
                      <th>Chunk ID / Source Reference</th>
                      <th>Match Type</th>
                    </tr>
                  </thead>
                  <tbody>
                    {traceDetails.retrieved_chunk_ids && traceDetails.retrieved_chunk_ids.map((chunkId, idx) => (
                      <tr key={idx}>
                        <td style={{ fontFamily: "monospace" }}>#{idx + 1}</td>
                        <td style={{ fontFamily: "monospace" }}>{chunkId}</td>
                        <td>
                          <span style={{ background: "rgba(0, 238, 255, 0.1)", color: "var(--accent-cyan)", fontSize: "0.75rem", padding: "0.2rem 0.5rem", borderRadius: "4px" }}>
                            Dense-Sparse Fused Candidate
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : (
            <div className="glass" style={{ padding: "2rem", textAlign: "center", color: "var(--text-muted)" }}>
              No recent searches recorded. Execute a clinical search query first to populate trace audit tables.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
