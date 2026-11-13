#!/usr/bin/env node
// Turns the recording (out/raw.webm + out/marks.json, from record.spec.ts) into
//   docs/media/demo.mp4  H.264, 1280x720, the whole walkthrough, waits for the model sped up
//   docs/media/demo.gif  960x540, the highlight between the "gif-start" and "gif-end" marks
// Needs ffmpeg and ffprobe on PATH. Usage: node demo/render.mjs [out dir] [media dir]
import { execFileSync } from "node:child_process";
import { readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.resolve(process.argv[2] ?? path.join(here, "out"));
const media = path.resolve(process.argv[3] ?? path.join(here, "..", "..", "..", "docs", "media"));
const raw = path.join(out, "raw.webm");
const marks = JSON.parse(readFileSync(path.join(out, "marks.json"), "utf8"));

const FPS = 25; // Playwright records at 25 frames per second
const MP4_LIMIT = 20e6;
const GIF_LIMIT = 8e6;
const GIF_MAX_SECONDS = 30;

const duration = Number(
  execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", raw]).toString().trim(),
);
if (marks.length < 2) throw new Error("marks.json needs at least a start and an end mark");

// Segments between consecutive marks, each at the speed of the mark that opens it.
const segments = [];
for (let i = 0; i < marks.length - 1; i++) {
  const start = marks[i].t;
  const end = Math.min(marks[i + 1].t, duration);
  if (end - start > 0.04) segments.push({ start, end, speed: marks[i].speed, label: marks[i].label });
}

// Where each mark lands in the rendered video.
const at = new Map();
let elapsed = 0;
for (const segment of segments) {
  if (!at.has(segment.label)) at.set(segment.label, elapsed);
  elapsed += (segment.end - segment.start) / segment.speed;
}
at.set(marks[marks.length - 1].label, elapsed);
for (const mark of marks) if (!at.has(mark.label)) at.set(mark.label, null);

const filter =
  segments
    .map((s, i) => `[0:v]trim=start=${s.start.toFixed(3)}:end=${s.end.toFixed(3)},setpts=(PTS-STARTPTS)/${s.speed}[v${i}]`)
    .join(";") +
  ";" +
  segments.map((_, i) => `[v${i}]`).join("") +
  `concat=n=${segments.length}:v=1:a=0,fps=${FPS},format=yuv420p[out]`;

const mp4 = path.join(media, "demo.mp4");
const ffmpeg = (args) => execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", ...args], { stdio: "inherit" });

ffmpeg([
  "-i", raw,
  "-filter_complex", filter,
  "-map", "[out]",
  "-c:v", "libx264", "-preset", "slow", "-crf", "24", "-tune", "stillimage",
  "-movflags", "+faststart",
  mp4,
]);

// The GIF: the permission story (a cited answer, then the same question without access).
const gifStart = at.get("gif-start");
const gifEnd = at.get("gif-end");
if (gifStart == null || gifEnd == null) throw new Error("marks.json has no gif-start/gif-end marks");
const gifLength = gifEnd - gifStart;
const gifSpeed = Math.max(1, gifLength / GIF_MAX_SECONDS);
const gif = path.join(media, "demo.gif");
const gifFilter =
  `trim=start=${gifStart.toFixed(3)}:end=${gifEnd.toFixed(3)},setpts=(PTS-STARTPTS)/${gifSpeed.toFixed(3)},` +
  "fps=10,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];" +
  "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle";
ffmpeg(["-i", mp4, "-filter_complex", gifFilter, "-loop", "0", gif]);

const size = (file) => statSync(file).size;
const report = {
  raw_seconds: Number(duration.toFixed(1)),
  mp4_seconds: Number(elapsed.toFixed(1)),
  mp4_bytes: size(mp4),
  gif_seconds: Number((gifLength / gifSpeed).toFixed(1)),
  gif_speed: Number(gifSpeed.toFixed(2)),
  gif_bytes: size(gif),
  chapters: Object.fromEntries([...at].map(([label, t]) => [label, t == null ? null : Number(t.toFixed(1))])),
};
console.log(JSON.stringify(report, null, 2));
if (report.mp4_bytes > MP4_LIMIT) throw new Error(`demo.mp4 is ${report.mp4_bytes} bytes, over ${MP4_LIMIT}`);
if (report.gif_bytes > GIF_LIMIT) throw new Error(`demo.gif is ${report.gif_bytes} bytes, over ${GIF_LIMIT}`);
