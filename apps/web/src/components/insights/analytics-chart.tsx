"use client";

import { ChartColumnBig, Table2 } from "lucide-react";
import { useId, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

import { useElementWidth } from "@/hooks/use-element-width";
import { chartTable, stackSegments, type ChartData } from "@/lib/analytics/chart-data";
import {
  barPath,
  chartGeometry,
  GAP,
  groupedBars,
  indexAt,
  LABEL_ANGLE,
  lastIndex,
  linePath,
  MAX_BAR,
  maxIndex,
  PLOT_HEIGHT,
  shortLabel,
  type Geometry,
} from "@/lib/analytics/chart-geometry";
import { formatValue } from "@/lib/analytics/format";
import { cn } from "@/lib/utils";

const color = (slot: number) => `var(--viz-${slot})`;
/** Points on a line get a marker each while they are few enough to read. */
const MAX_MARKERS = 16;
/** Up to this many lines are named at their end, so no line is identified by colour alone. */
const MAX_DIRECT_LABELS = 4;
/** Closest two end labels may be, vertically, in pixels. */
const LABEL_SPACING = 13;

/**
 * A chart from the answer's chart spec: line, grouped bar or stacked bar, drawn as SVG.
 * Hover or arrow keys show every series at one x; a table view lists every value.
 */
export function AnalyticsChart({ data }: { data: ChartData }) {
  const [view, setView] = useState<"chart" | "table">("chart");
  const titleId = useId();
  return (
    <figure aria-labelledby={titleId} className="flex min-w-0 flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <figcaption id={titleId} className="text-sm font-medium">
          {data.title}
        </figcaption>
        <ViewToggle view={view} onChange={setView} />
      </div>
      {data.series.length > 1 ? <Legend data={data} /> : null}
      {view === "chart" ? <Plot data={data} /> : <ChartDataTable data={data} />}
      {data.folded > 0 ? (
        <p className="text-xs text-muted-foreground">
          The {data.folded} smallest series are combined as “Other”; the query&apos;s table lists each one.
        </p>
      ) : null}
      {data.omitted.length > 0 ? (
        <p className="text-xs text-muted-foreground">
          Not drawn: {data.omitted.join(", ")}, in another unit than {data.series[0]?.label ?? "the chart"}. The query&apos;s table has the values.
        </p>
      ) : null}
    </figure>
  );
}

/** "Show as: Chart | Table". Every chart has a table twin; this switches between them. */
export function ViewToggle({ view, onChange }: { view: "chart" | "table"; onChange: (view: "chart" | "table") => void }) {
  const options = [
    { value: "chart", label: "Chart", icon: ChartColumnBig },
    { value: "table", label: "Table", icon: Table2 },
  ] as const;
  return (
    <div role="group" aria-label="Show as" className="inline-flex rounded-lg bg-muted p-0.5">
      {options.map(({ value, label, icon: Icon }) => (
        <button
          key={value}
          type="button"
          aria-pressed={view === value}
          onClick={() => onChange(value)}
          className={cn(
            "inline-flex h-6 items-center gap-1 rounded-md px-2 text-xs font-medium text-foreground/75 transition-colors",
            "hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none",
            view === value && "bg-background text-foreground shadow-sm",
          )}
        >
          <Icon className="size-3.5" aria-hidden="true" />
          {label}
        </button>
      ))}
    </div>
  );
}

function Legend({ data }: { data: ChartData }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs" aria-label="Legend">
      {data.series.map((series) => (
        <li key={series.key} className="flex items-center gap-1.5 text-muted-foreground">
          {data.type === "line" ? (
            <span aria-hidden="true" className="h-0.5 w-3.5 rounded-full" style={{ background: color(series.slot) }} />
          ) : (
            <span aria-hidden="true" className="size-2.5 rounded-[3px]" style={{ background: color(series.slot) }} />
          )}
          <span className="text-foreground">{series.label}</span>
        </li>
      ))}
    </ul>
  );
}

interface Readout {
  label: string;
  rows: { key: string; label: string; slot: number; value: number | null }[];
  total: number | null;
}

function readout(data: ChartData, index: number): Readout {
  const rows = data.series.map((s) => ({ key: s.key, label: s.label, slot: s.slot, value: s.values[index] }));
  const present = rows.map((r) => r.value).filter((v): v is number => v !== null);
  return {
    label: data.categories[index]?.label ?? "",
    // A stack reads top to bottom, so its tooltip does too.
    rows: data.type === "stacked_bar" ? rows.reverse() : rows,
    total: data.type === "stacked_bar" && rows.length > 1 && present.length > 0 ? present.reduce((a, b) => a + b, 0) : null,
  };
}

function Plot({ data }: { data: ChartData }) {
  const ref = useRef<HTMLDivElement>(null);
  const width = useElementWidth(ref);
  const [active, setActive] = useState<number | null>(null);
  const hintId = useId();
  const count = data.categories.length;
  const fmt = (value: number, style: "full" | "compact" = "compact") => formatValue(value, data.yFormat, style, data.percentScale);

  const single = data.series.length === 1 ? data.series[0] : null;
  const end = data.type === "line" && single ? lastIndex(single.values) : -1;
  const endLabel = single && end >= 0 ? fmt(single.values[end]!) : null;
  const named = data.type === "line" && data.series.length > 1 && data.series.length <= MAX_DIRECT_LABELS;
  const names = named ? data.series.map((s) => shortLabel(s.label)) : [];
  const longestName = names.reduce<string | null>((a, b) => (a === null || b.length > a.length ? b : a), null);
  const g = chartGeometry(data, width, endLabel ?? longestName);

  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const px = event.clientX - rect.left;
    if (px < g.margin.left - g.band / 2 || px > g.margin.left + g.plotWidth + g.band / 2) {
      setActive(null);
      return;
    }
    setActive(indexAt(px, g, count, data.type));
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const moves: Record<string, (current: number | null) => number | null> = {
      ArrowRight: (c) => (c === null ? 0 : Math.min(count - 1, c + 1)),
      ArrowLeft: (c) => (c === null ? count - 1 : Math.max(0, c - 1)),
      Home: () => 0,
      End: () => count - 1,
      Escape: () => null,
    };
    const move = moves[event.key];
    if (!move) return;
    event.preventDefault();
    setActive(move);
  }

  const current = active !== null && active < count ? readout(data, active) : null;
  const announcement = current
    ? `${current.label}: ${current.rows.map((r) => `${r.label} ${r.value === null ? "no value" : fmt(r.value, "full")}`).join(", ")}${
        current.total !== null ? `; total ${fmt(current.total, "full")}` : ""
      }`
    : "";

  const tooltipX = active !== null ? g.x(active) : 0;
  const flip = tooltipX + 16 + 220 > g.width;

  return (
    <div
      ref={ref}
      className="relative w-full rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
      role="group"
      aria-roledescription="chart"
      aria-label={data.title}
      aria-describedby={hintId}
      tabIndex={0}
      onKeyDown={onKeyDown}
      onPointerMove={onPointerMove}
      onPointerLeave={() => setActive(null)}
      onBlur={() => setActive(null)}
    >
      <p id={hintId} className="sr-only">
        Use the left and right arrow keys to read each {data.xLabel.toLowerCase()}. The table view lists every value.
      </p>
      <svg width={g.width} height={g.height} aria-hidden="true" className="block max-w-full overflow-visible select-none">
        <Grid g={g} />
        {active !== null && data.type !== "line" ? (
          <rect x={g.x(active) - g.band / 2} y={g.margin.top} width={g.band} height={PLOT_HEIGHT} className="fill-muted" />
        ) : null}
        {data.type === "line" ? (
          <LineMarks data={data} g={g} active={active} end={end} endLabel={endLabel} names={names} />
        ) : data.type === "bar" ? (
          <BarMarks data={data} g={g} fmt={fmt} />
        ) : (
          <StackedMarks data={data} g={g} />
        )}
        <XLabels data={data} g={g} />
      </svg>
      {current ? (
        <div
          className="pointer-events-none absolute z-10 w-max max-w-56 rounded-md border bg-popover px-2.5 py-2 text-xs text-popover-foreground shadow-md"
          style={{ left: flip ? tooltipX - 12 : tooltipX + 12, top: g.margin.top, transform: flip ? "translateX(-100%)" : undefined }}
          aria-hidden="true"
        >
          <p className="mb-1 text-muted-foreground">{current.label}</p>
          <ul className="flex flex-col gap-0.5">
            {current.rows.map((row) => (
              <li key={row.key} className="flex items-center gap-2">
                <span className="h-0.5 w-3 shrink-0 rounded-full" style={{ background: color(row.slot) }} />
                <span className="font-semibold tabular-nums">{row.value === null ? "—" : fmt(row.value, "full")}</span>
                <span className="truncate text-muted-foreground">{row.label}</span>
              </li>
            ))}
          </ul>
          {current.total !== null ? (
            <p className="mt-1 border-t pt-1">
              <span className="font-semibold tabular-nums">{fmt(current.total, "full")}</span> <span className="text-muted-foreground">total</span>
            </p>
          ) : null}
        </div>
      ) : null}
      <p className="sr-only" aria-live="polite">
        {announcement}
      </p>
    </div>
  );
}

function Grid({ g }: { g: Geometry }) {
  return (
    <g>
      {g.ticks.map((tick, i) => (
        <g key={tick}>
          <line
            x1={g.margin.left}
            x2={g.margin.left + g.plotWidth}
            y1={g.y(tick)}
            y2={g.y(tick)}
            strokeWidth={1}
            shapeRendering="crispEdges"
            style={{ stroke: tick === 0 ? "var(--viz-axis)" : "var(--viz-grid)" }}
          />
          <text x={g.margin.left - 8} y={g.y(tick)} dy="0.32em" textAnchor="end" className="fill-muted-foreground text-[11px] tabular-nums">
            {g.tickLabels[i]}
          </text>
        </g>
      ))}
    </g>
  );
}

function XLabels({ data, g }: { data: ChartData; g: Geometry }) {
  const y = g.margin.top + PLOT_HEIGHT + 18;
  if (g.rotateLabels) {
    const top = g.margin.top + PLOT_HEIGHT + 10;
    return (
      <g>
        {data.categories.map((category, i) => (
          <text
            key={category.key}
            x={g.x(i)}
            y={top}
            dy="0.32em"
            textAnchor="end"
            transform={`rotate(-${LABEL_ANGLE} ${g.x(i)} ${top})`}
            className="fill-muted-foreground text-[11px]"
          >
            {shortLabel(category.label)}
          </text>
        ))}
      </g>
    );
  }
  return (
    <g>
      {data.categories.map((category, i) =>
        i % g.labelStep === 0 ? (
          <text key={category.key} x={g.x(i)} y={y} textAnchor="middle" className="fill-muted-foreground text-[11px]">
            {category.label}
          </text>
        ) : null,
      )}
    </g>
  );
}

/** Where each line's name goes at its end: at its last value, pushed apart so none overlap. */
function nameSlots(data: ChartData, g: Geometry): { key: string; x: number; y: number; index: number }[] {
  const slots = data.series
    .map((s, index) => {
      const last = lastIndex(s.values);
      return last < 0 ? null : { key: s.key, x: g.x(last), y: g.y(s.values[last]!), index };
    })
    .filter((slot): slot is { key: string; x: number; y: number; index: number } => slot !== null)
    .sort((a, b) => a.y - b.y);
  for (let i = 1; i < slots.length; i++) {
    if (slots[i].y - slots[i - 1].y < LABEL_SPACING) slots[i].y = slots[i - 1].y + LABEL_SPACING;
  }
  return slots;
}

function LineMarks({
  data,
  g,
  active,
  end,
  endLabel,
  names,
}: {
  data: ChartData;
  g: Geometry;
  active: number | null;
  end: number;
  endLabel: string | null;
  /** Each series' name, drawn at the end of its line (2 to 4 series); empty otherwise. */
  names: readonly string[];
}) {
  const showAll = data.categories.length <= MAX_MARKERS;
  return (
    <g>
      {active !== null ? (
        <line
          x1={g.x(active)}
          x2={g.x(active)}
          y1={g.margin.top}
          y2={g.margin.top + PLOT_HEIGHT}
          strokeWidth={1}
          shapeRendering="crispEdges"
          style={{ stroke: "var(--viz-axis)" }}
        />
      ) : null}
      {data.series.map((series) => (
        <path
          key={series.key}
          d={linePath(series.values, g.x, g.y)}
          fill="none"
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
          style={{ stroke: color(series.slot) }}
        />
      ))}
      {data.series.map((series) => {
        const last = lastIndex(series.values);
        return series.values.map((value, i) =>
          value !== null && (showAll || i === last || i === active) ? (
            <circle
              key={`${series.key}-${i}`}
              cx={g.x(i)}
              cy={g.y(value)}
              r={i === active ? 5 : 4}
              strokeWidth={2}
              className="stroke-card"
              style={{ fill: color(series.slot) }}
            />
          ) : null,
        );
      })}
      {endLabel && end >= 0 && data.series[0].values[end] !== null ? (
        <text x={g.x(end) + 9} y={g.y(data.series[0].values[end]!)} dy="0.32em" className="fill-foreground text-[11px] font-medium tabular-nums">
          {endLabel}
        </text>
      ) : null}
      {names.length > 0
        ? nameSlots(data, g).map((slot) => (
            <text key={`name-${slot.key}`} x={slot.x + 9} y={slot.y} dy="0.32em" className="fill-foreground text-[11px] font-medium" data-series-name="">
              {names[slot.index]}
            </text>
          ))
        : null}
    </g>
  );
}

function BarMarks({ data, g, fmt }: { data: ChartData; g: Geometry; fmt: (value: number) => string }) {
  const { width, offsets } = groupedBars(g.band, data.series.length);
  const base = g.y(0);
  // One series: label only the largest bar (selective labels; the axis and table carry the rest).
  const labelAt = data.series.length === 1 ? maxIndex(data.series[0].values) : -1;
  return (
    <g>
      {data.categories.map((category, i) =>
        data.series.map((series, s) => {
          const value = series.values[i];
          if (value === null) return null;
          const x = g.x(i) + offsets[s];
          return <path key={`${category.key}-${series.key}`} d={barPath(x, base, g.y(value), width)} style={{ fill: color(series.slot) }} />;
        }),
      )}
      {labelAt >= 0 ? (
        <text
          x={g.x(labelAt)}
          y={g.y(data.series[0].values[labelAt]!) - 6}
          textAnchor="middle"
          className="fill-foreground text-[11px] font-medium tabular-nums"
        >
          {fmt(data.series[0].values[labelAt]!)}
        </text>
      ) : null}
    </g>
  );
}

function StackedMarks({ data, g }: { data: ChartData; g: Geometry }) {
  const width = Math.max(2, Math.min(MAX_BAR, g.band * 0.6));
  return (
    <g>
      {data.categories.map((category, i) =>
        stackSegments(data, i).map((segment) => {
          const up = segment.value > 0;
          // A 2px surface gap between touching segments: inset each inner edge by 1px.
          const inset = (pixel: number, towardEnd: boolean) => pixel + (towardEnd === up ? -GAP / 2 : GAP / 2);
          const base = segment.from === 0 ? g.y(0) : inset(g.y(segment.from), true);
          const end = segment.outermost ? g.y(segment.to) : inset(g.y(segment.to), false);
          return (
            <path
              key={`${category.key}-${segment.seriesKey}`}
              d={barPath(g.x(i) - width / 2, base, end, width, segment.outermost)}
              style={{ fill: color(segment.slot) }}
            />
          );
        }),
      )}
    </g>
  );
}

/** The chart's values as a table (the accessible twin of the chart). */
function ChartDataTable({ data }: { data: ChartData }) {
  const table = chartTable(data);
  return (
    <div className="max-h-80 overflow-auto rounded-lg border">
      <table className="w-full text-sm">
        <caption className="sr-only">{data.title}</caption>
        <thead className="sticky top-0 bg-muted/80 backdrop-blur">
          <tr>
            {table.header.map((name, i) => (
              <th key={`${name}-${i}`} scope="col" className={cn("px-3 py-1.5 font-medium whitespace-nowrap", i === 0 ? "text-left" : "text-right")}>
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, r) => (
            <tr key={`${row.label}-${r}`} className="border-t">
              <th scope="row" className="px-3 py-1.5 text-left font-normal whitespace-nowrap">
                {row.label}
              </th>
              {row.values.map((value, c) => (
                <td key={c} className="px-3 py-1.5 text-right whitespace-nowrap tabular-nums">
                  {value === null ? "—" : formatValue(value, data.yFormat, "full", data.percentScale)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
