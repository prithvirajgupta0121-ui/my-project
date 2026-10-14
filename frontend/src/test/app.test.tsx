import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../App";
import { HEALTH, mockFetch, SESSION } from "./mockApi";

const DASH = {
  missions: { total: 3, active: 2, by_status: { REVIEW: 1, PROCESSING: 1, COMPLETED: 1 }, recent: [
    { id: 1, code: "AIM-2026-0930-001", name: "Alpha Survey", status: "REVIEW", progress: 100, current_stage: null, is_demo: false, lat: 1, lon: 1, updated_at: null }] },
  detections: { total: 1234, by_class: { VEHICLE: 1000, PERSON: 234 } },
  tracks: { total: 50, active: 4, active_live: 0, active_processing: 4 },
  events: { total: 80, pending_review: 70, priority_pending: 9, last_24h: 80 },
  live: { streams: 0, active_tracks: 0, fps: 0, sources: [] }, queue: { depth: 1, queued: [], running: 2 },
  health: { overall: "OPERATIONAL", components: [] },
  system: { cpu_percent: 12, memory_percent: 40, disk_percent: 20, inference_ms_avg: 30 },
};

describe("authentication gate", () => {
  it("shows login with ENTER DEMO MODE when not authenticated", async () => {
    mockFetch({ "/api/auth/me": { __status: 401, body: { error: { code: "NOT_AUTHENTICATED", message: "x" } } }, "/api/auth/config": { demo_enabled: true, version: "1.0.0" } });
    render(<App />);
    expect(await screen.findByText("ENTER DEMO MODE")).toBeInTheDocument();
    expect(screen.getByText("RESTRICTED SYSTEM")).toBeInTheDocument();
  });

  it("displays structured login errors", async () => {
    mockFetch({
      "/api/auth/me": { __status: 401, body: { error: { code: "NOT_AUTHENTICATED", message: "x" } } },
      "/api/auth/config": { demo_enabled: false },
      "/api/auth/login": { __status: 401, body: { error: { code: "INVALID_CREDENTIALS", message: "Invalid username or password." } } },
    });
    render(<App />);
    await userEvent.type(await screen.findByLabelText("Username"), "admin");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: /AUTHENTICATE/ }));
    expect(await screen.findByText("Invalid username or password.")).toBeInTheDocument();
  });

  it("requires credentials before submitting", async () => {
    const f = mockFetch({ "/api/auth/me": { __status: 401, body: { error: { code: "NOT_AUTHENTICATED", message: "x" } } }, "/api/auth/config": {} });
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /AUTHENTICATE/ }));
    expect(await screen.findByText("Enter username and password.")).toBeInTheDocument();
    expect(f.mock.calls.some((c) => String(c[0]).includes("/api/auth/login"))).toBe(false);
  });
});

describe("command center & navigation", () => {
  it("renders dashboard KPIs from live API state and navigates", async () => {
    mockFetch({ "/api/auth/me": SESSION, "/api/dashboard": DASH, "/api/system/health": HEALTH, "/api/geo/overview": { missions: [], events: [] }, "/api/auth/config": {}, "/api/missions": [] });
    window.location.hash = "/command";
    render(<App />);
    expect(await screen.findByText("1,234")).toBeInTheDocument();
    expect(screen.getByText("AI DETECTIONS")).toBeInTheDocument();
    expect(screen.getByText("AIM-2026-0930-001")).toBeInTheDocument();
    expect(screen.getAllByText("LOCAL PROCESSING").length).toBeGreaterThan(0);
    expect(screen.getByText("NO CLOUD UPLOAD")).toBeInTheDocument();
    // role-gated nav: analyst has no SECURITY entry
    expect(screen.queryByTitle("SECURITY")).toBeNull();
    expect(screen.getByTitle("ANALYTICS")).toBeInTheDocument();
    await act(async () => { fireEvent.click(screen.getByTitle("MISSIONS")); });
    await waitFor(() => expect(window.location.hash).toBe("#/missions"));
  });

  it("blocks sections the role cannot access", async () => {
    mockFetch({ "/api/auth/me": SESSION, "/api/system/health": HEALTH });
    window.location.hash = "/security";
    render(<App />);
    expect(await screen.findByText(/does not have access/)).toBeInTheDocument();
  });
});
