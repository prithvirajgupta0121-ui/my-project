import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import PipelineView from "../components/PipelineView";
import TacticalMap, { trackPopup } from "../components/TacticalMap";
import { AIObservation } from "../components/ui";
import { checkFile, Uploader, validateMission } from "../pages/Missions";
import { mockFetch } from "./mockApi";

describe("mission form validation", () => {
  const base = { name: "Alpha", latitude: "12.5", longitude: "-35", altitude_m: "120", start_time: "", end_time: "" };
  it("accepts a valid mission", () => expect(validateMission(base)).toEqual({}));
  it("rejects invalid GPS", () => {
    expect(validateMission({ ...base, latitude: "95" }).latitude).toMatch(/INVALID GPS/);
    expect(validateMission({ ...base, longitude: "abc" }).longitude).toMatch(/INVALID GPS/);
    expect(validateMission({ ...base, latitude: "0", longitude: "0" }).latitude).toMatch(/0,0/);
  });
  it("requires a name and ordered times", () => {
    expect(validateMission({ ...base, name: "" }).name).toBeTruthy();
    expect(validateMission({ ...base, start_time: "2026-01-02T10:00", end_time: "2026-01-02T09:00" }).end_time).toBeTruthy();
  });
});

describe("upload validation", () => {
  const f = (name: string, size = 10) => new File([new Uint8Array(size)], name);
  it("classifies files", () => {
    expect(checkFile(f("a.mp4"))).toBeNull();
    expect(checkFile(f("a.WEBP"))).toBeNull();
    expect(checkFile(f("a.exe"))).toMatch(/UNSUPPORTED/);
    expect(checkFile(f("a.jpg", 0))).toMatch(/INVALID FILE/);
    expect(checkFile(f("a.png", 3 * 1024 * 1024), 1)).toMatch(/TOO LARGE/);
  });
  it("shows per-file status in the uploader", async () => {
    mockFetch({});
    render(<Uploader missionId={1} onDone={() => {}} />);
    const input = screen.getByTestId("file-input") as HTMLInputElement;
    await userEvent.upload(input, [f("clip.mp4"), f("bad.txt")], { applyAccept: false });
    expect(await screen.findByText("READY")).toBeInTheDocument();
    expect(screen.getByText(/UNSUPPORTED FILE TYPE/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /UPLOAD 1 FILE/ })).toBeEnabled();
  });
});

describe("pipeline visualisation", () => {
  it("renders all ten stages with state", () => {
    render(<PipelineView stages={{ DRONE_DATA: { state: "DONE", metrics: { files: 1 } }, AI_DETECT: { state: "RUNNING", metrics: {} }, REPORT: { state: "FAILED", metrics: {} } }} />);
    for (const s of ["DRONE DATA", "INGESTION", "PREPROCESS", "AI DETECT", "TRACKING", "SCENE AI", "EVENT AI", "GEO ANALYSIS", "INTELLIGENCE", "REPORT"]) {
      expect(screen.getByText(s)).toBeInTheDocument();
    }
    expect(screen.getByTestId("stage-AI_DETECT")).toHaveAttribute("data-state", "RUNNING");
    expect(screen.getByTestId("stage-REPORT")).toHaveAttribute("data-state", "FAILED");
  });
});

describe("human-in-the-loop framing", () => {
  it("never presents AI output as truth", () => {
    render(<AIObservation confidence={0.87} status="UNVERIFIED" />);
    expect(screen.getByText("AI OBSERVATION")).toBeInTheDocument();
    expect(screen.getByText("CONFIDENCE: 87%")).toBeInTheDocument();
    expect(screen.getByText(/REQUIRES HUMAN REVIEW/)).toBeInTheDocument();
  });
});

describe("tactical map", () => {
  it("mounts offline and builds track popups", () => {
    render(<TacticalMap tracks={[{ id: 1, track_no: 24, cls: "VEHICLE", avg_confidence: 0.91, first_seen_s: 13, last_seen_s: 62, observations: 86, review_status: "UNVERIFIED", trajectory: [[0, 12.5, -35], [1, 12.5001, -35.0001]] }]} fitKey={1} />);
    expect(screen.getByTestId("tactical-map")).toBeInTheDocument();
    expect(screen.getByText(/LOCAL GRID · OFFLINE MAP/)).toBeInTheDocument();
    const html = trackPopup({ id: 1, track_no: 24, cls: "VEHICLE", avg_confidence: 0.91, first_seen_s: 13, last_seen_s: 62, observations: 86, review_status: "UNVERIFIED", trajectory: [] });
    expect(html).toContain("TRACK #024");
    expect(html).toContain("91%");
    expect(html).toContain("OBSERVATIONS");
    expect(html).toContain("REQUIRED");
  });
  it("escapes untrusted text in popups", () => {
    const html = trackPopup({ id: 1, track_no: 1, cls: "<img src=x onerror=alert(1)>", avg_confidence: 0.5, first_seen_s: 0, last_seen_s: 1, observations: 1, review_status: "UNVERIFIED", trajectory: [] });
    expect(html).not.toContain("<img src=x");
  });
});
