"use client";

import { AccessTokensCard } from "@/components/settings/access-tokens";
import { SignedInGate } from "@/components/auth/signed-in-gate";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { DEPARTMENT_ROLE_LABELS, ORG_ROLE_LABELS, type Me } from "@/lib/session";

function ProfileCard({ me }: { me: Me }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Profile</CardTitle>
        <CardDescription>Managed by your organization&apos;s administrators and identity provider.</CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[10rem_1fr]">
          <dt className="text-muted-foreground">Name</dt>
          <dd>{me.display_name}</dd>
          <dt className="text-muted-foreground">Email</dt>
          <dd>{me.email}</dd>
          {me.title ? (
            <>
              <dt className="text-muted-foreground">Job title</dt>
              <dd>{me.title}</dd>
            </>
          ) : null}
          <dt className="text-muted-foreground">Organization</dt>
          <dd className="flex items-center gap-2">
            {me.tenant.name}
            <Badge variant="secondary">{ORG_ROLE_LABELS[me.org_role]}</Badge>
          </dd>
          <dt className="text-muted-foreground">Departments</dt>
          <dd>
            {me.departments.length
              ? me.departments.map((d) => `${d.name} (${DEPARTMENT_ROLE_LABELS[d.role]})`).join(", ")
              : "None"}
          </dd>
          <dt className="text-muted-foreground">Groups</dt>
          <dd>{me.groups.length ? me.groups.map((g) => g.name).join(", ") : "None"}</dd>
        </dl>
      </CardContent>
    </Card>
  );
}

export function SettingsView() {
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-6">
      <header>
        <h2 className="text-xl font-semibold tracking-tight">Settings</h2>
        <p className="text-sm text-muted-foreground">Your profile and access tokens.</p>
      </header>
      <SignedInGate purpose="see your settings">
        {(me) => (
          <>
            <ProfileCard me={me} />
            <AccessTokensCard />
          </>
        )}
      </SignedInGate>
    </div>
  );
}
