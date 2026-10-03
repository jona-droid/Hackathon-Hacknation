// Backend base URL. Set VITE_API_URL at build time (e.g. on Render); defaults to local dev.
export const API_URL = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/+$/, "");

// Same host as the API, with http(s) swapped for ws(s).
export const WS_URL = API_URL.replace(/^http/, "ws") + "/ws";
