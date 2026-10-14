import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AuthProvider } from "../lib/auth";
import Reports from "../pages/Reports";
import { mockFetch, SESSION } from "./mockApi";

const PREVIEW = {
  report: { notice: "AI-GENERATED / REQUIRES HUMAN REVIEW", demo: false, system: "AEGIS", version: "1.0.0" },
  mission_summary: { mission_id: "AIM-2026-0930-001", name: "Alpha", status: "REVIEW", operator: "Op", drone: "UAS", altitude_m: 100, media: [] },
  mission_timeline: [{ t: null, label: "Mission created", at: "2026-09-30T05:00:00Z" }],
  detection_summary: { total: 10, by_class: [{ class: "VEHICLE", count: 10, avg_confidence: 0.8 }], provider: { name: "classical_cv", assurance: "HEURISTIC" } },
  tracking_summary: { total: 2 }, event_summary: { total: 1, by_type: { OBJECT_APPEARED: 1 }, review: { UNVERIFIED: 1 } },
  geospatial_summary: { route_length_m: 400, route_points: 20, position_source: "VISUAL_ODOMETRY", geolocated_events: 1, method: "m" },
  evidence_frames: [], analyst_notes: [], system_metadata: { storage: "LOCAL", cloud_upload: "NONE" },
};

describe("report center", () => {
  it("shows the mandatory notice, sections and export actions", async () => {
    mockFetch({ "/api/auth/me": SESSION, "/api/missions?": [{ id: 1, code: "AIM-2026-0930-001", name: "Alpha", status: "REVIEW", is_demo: false }],
      "/api/reports": [], "/api/missions/1/report-preview": PREVIEW });
    window.location.hash = "/reports?mission=1";
    render(<AuthProvider><Reports /></AuthProvider>);
    expect(await screen.findByText("EXPORT PDF")).toBeInTheDocument();
    expect(screen.getAllByText("AI-GENERATED / REQUIRES HUMAN REVIEW").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /JSON/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /CSV/ })).toBeInTheDocument();
    expect(screen.getByText(/all observations remain UNVERIFIED/)).toBeInTheDocument();
  });
});
