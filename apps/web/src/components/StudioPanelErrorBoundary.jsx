import { Component } from "react";

/**
 * Catches render errors in a major Studio panel so the rail + shell stay usable.
 * Resets when `resetKey` changes (e.g. user switches page on the vertical rail).
 */
export class StudioPanelErrorBoundary extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("StudioPanelErrorBoundary", error, info?.componentStack);
  }

  componentDidUpdate(prevProps) {
    if (prevProps.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  render() {
    if (this.state.error) {
      const msg =
        this.state.error && typeof this.state.error.message === "string"
          ? this.state.error.message
          : String(this.state.error);
      return (
        <section className="panel studio-error-boundary" role="alert">
          <h2 className="studio-error-boundary__title">This page hit an error</h2>
          <p className="subtle">
            The rest of the app should still work. Switch to another tab on the left, or try again below.
          </p>
          <pre className="studio-error-boundary__detail">{msg}</pre>
          <div className="studio-error-boundary__actions">
            <button type="button" onClick={() => this.setState({ error: null })}>
              Try again
            </button>
          </div>
        </section>
      );
    }
    return this.props.children;
  }
}
