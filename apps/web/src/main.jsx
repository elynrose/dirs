import React from "react";
import ReactDOM from "react-dom/client";
import "@fortawesome/fontawesome-free/css/all.min.css";
import App from "./App.jsx";
import "./index.css";

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("Directely UI error:", error, info);
  }

  render() {
    if (this.state.error) {
      const msg = String(this.state.error?.message || this.state.error);
      return (
        <div
          style={{
            padding: 24,
            color: "#fca5a5",
            fontFamily: "system-ui, sans-serif",
            maxWidth: 720,
            lineHeight: 1.5,
          }}
        >
          <h1 style={{ fontSize: "1rem", margin: "0 0 12px", color: "#fef2f2" }}>
            Directely UI failed to render
          </h1>
          <p style={{ margin: "0 0 8px", color: "#d4d4d4", fontSize: "0.9rem" }}>
            Open the browser console (DevTools) for the full stack trace. Common causes: opening{" "}
            <code style={{ color: "#fff" }}>index.html</code> as a file instead of via{" "}
            <code style={{ color: "#fff" }}>npm run dev</code> / <code style={{ color: "#fff" }}>npm run preview</code>
            , or a JavaScript error during startup.
          </p>
          <pre
            style={{
              whiteSpace: "pre-wrap",
              fontSize: "0.82rem",
              margin: 0,
              padding: 12,
              background: "rgb(0 0 0 / 40%)",
              borderRadius: 8,
              border: "1px solid rgb(255 255 255 / 12%)",
            }}
          >
            {msg}
          </pre>
        </div>
      );
    }
    return this.props.children;
  }
}

const el = document.getElementById("root");
if (!el) {
  document.body.innerHTML =
    '<p style="padding:24px;color:#fca5a5;font-family:system-ui">Missing #root — check index.html.</p>';
} else {
  ReactDOM.createRoot(el).render(
    <React.StrictMode>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </React.StrictMode>,
  );
}
