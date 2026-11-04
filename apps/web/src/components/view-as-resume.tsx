"use client";

import { useEffect } from "react";

import { takePendingViewAs, viewAsLoginUrl } from "@/lib/demo";

/** Second half of the demo "view as" switch: after Keycloak's logout lands here, sign in as the chosen account. */
export function ViewAsResume() {
  useEffect(() => {
    const email = takePendingViewAs(window.sessionStorage);
    if (email) window.location.replace(viewAsLoginUrl(email));
  }, []);
  return null;
}
