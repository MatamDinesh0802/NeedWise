/**
 * Copy and small rules for the Insights page: question validation, the waiting state, why
 * the page is unavailable, and status labels. Framework-free and unit-tested.
 */

import { QUESTION_MAX_LENGTH, type AnalyticsModelUnavailable, type AnswerStatus } from "@/lib/analytics/api";
import type { OrgRole } from "@/lib/session";

export function questionError(question: string): string | null {
  const trimmed = question.trim();
  if (!trimmed) return "Type a question first.";
  if (trimmed.length > QUESTION_MAX_LENGTH) return `Keep it under ${QUESTION_MAX_LENGTH.toLocaleString("en")} characters.`;
  return null;
}

/**
 * What the waiting state says. There's no streaming: one request runs the whole pipeline, so this
 * describes the steps it takes instead of pretending to track them.
 */
export function waitingMessage(elapsedSeconds: number): { title: string; detail: string } {
  if (elapsedSeconds < 30) {
    return {
      title: "Working on your question",
      detail: "Answers usually take 10–30 seconds: it writes SQL against the data model, runs it with your row access, and checks every figure in the answer against the results.",
    };
  }
  if (elapsedSeconds < 90) {
    return {
      title: "Still working",
      detail: "This one is taking longer than usual, often because it needs several queries. You can keep waiting, or stop: a finished answer is saved in your history either way.",
    };
  }
  return {
    title: "Taking much longer than usual",
    detail: "The server may be busy. Stop waiting and look in your history in a minute; if nothing appears, ask again.",
  };
}

export interface UnavailableCopy {
  title: string;
  description: string;
  /** Link for people who can fix it (admins: the setup page). */
  action: { href: string; label: string } | null;
}

export function unavailableCopy(model: AnalyticsModelUnavailable, role: OrgRole | null | undefined): UnavailableCopy {
  const canManage = role === "owner" || role === "admin";
  if (model.reason === "not_configured") {
    return {
      title: "Insights isn't set up yet",
      description: canManage
        ? "Upload the data (CSV or Excel tables) and write the data model that says what people may ask about, in Admin › Analytics."
        : "An administrator has to upload the company data and describe it in a data model first. Ask an owner or admin of your organization.",
      action: canManage ? { href: "/admin/analytics", label: "Set up analytics" } : null,
    };
  }
  return {
    title: "You don't have access to company data",
    description: canManage
      ? "The data model lists who may query it (its access list), and you're not on it. Add yourself or your department in Admin › Analytics if you should be."
      : "The data model lists the departments, groups and people who may query it, and you're not on it. Ask your manager or an administrator if you need access.",
    action: canManage ? { href: "/admin/analytics", label: "Open the data model" } : null,
  };
}

export const STATUS_LABELS: Record<AnswerStatus, string> = {
  answered: "Answered",
  no_access: "No access",
  failed: "Failed",
};

/** "2/3 figures verified" for history rows; null when there was nothing to check. */
export function verifiedLabel(verified: number, total: number): string | null {
  return total === 0 ? null : `${verified}/${total} figures verified`;
}

/**
 * What to suggest under a failed answer's error. The API's errors already say what to do when
 * the cause is not the question (the model is unavailable: "Try again later"; too slow; an
 * unusable reply): then nothing is added. Only an error that names no remedy gets the hint to
 * rephrase.
 */
export function failureAdvice(error: string | null | undefined): string | null {
  const text = (error ?? "").toLowerCase();
  if (/\bunavailable\b|\btry (again|asking again|a narrower)\b|\btoo long\b|\bbusy\b/.test(text)) return null;
  return "Try rephrasing it, or name the metric and period you mean (for example “revenue by month in 2026”).";
}
