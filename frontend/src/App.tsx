import { lazy, Suspense } from "react";
import Shell from "./components/Shell";
import { ErrorNote, ScanLoader } from "./components/ui";
import { AuthProvider, useAuth } from "./lib/auth";
import { FeedProvider } from "./lib/feed";
import { useRoute } from "./lib/hooks";
import Login from "./pages/Login";
import Command from "./pages/Command";

const LiveFeed = lazy(() => import("./pages/LiveFeed"));
const Missions = lazy(() => import("./pages/Missions"));
const IntelMap = lazy(() => import("./pages/IntelMap"));
const Analysis = lazy(() => import("./pages/Analysis"));
const Tracking = lazy(() => import("./pages/Tracking"));
const Events = lazy(() => import("./pages/Events"));
const Analytics = lazy(() => import("./pages/Analytics"));
const Reports = lazy(() => import("./pages/Reports"));
const Security = lazy(() => import("./pages/Security"));
const SystemPage = lazy(() => import("./pages/System"));
const SettingsPage = lazy(() => import("./pages/Settings"));

const ROUTES: Record<string, { el: React.ComponentType; perm?: string }> = {
  command: { el: Command }, live: { el: LiveFeed, perm: "live:view" }, missions: { el: Missions }, map: { el: IntelMap },
  analysis: { el: Analysis }, tracking: { el: Tracking }, events: { el: Events }, analytics: { el: Analytics, perm: "analytics:read" },
  reports: { el: Reports, perm: "report:read" }, security: { el: Security, perm: "audit:read" }, system: { el: SystemPage }, settings: { el: SettingsPage },
};

function Routed() {
  const { parts } = useRoute();
  const { can } = useAuth();
  const r = ROUTES[parts[0] || "command"] || ROUTES.command;
  if (r.perm && !can(r.perm)) return <div className="p-4"><ErrorNote error={{ code: "PERMISSION_DENIED", message: "Your role does not have access to this section." } as any} /></div>;
  const El = r.el;
  return <Suspense fallback={<ScanLoader label="LOADING MODULE" />}><El /></Suspense>;
}

function Gate() {
  const { session, loading } = useAuth();
  if (loading) return <div className="flex h-full items-center justify-center"><ScanLoader label="VERIFYING SESSION" /></div>;
  if (!session) return <Login />;
  return (
    <FeedProvider enabled>
      <Shell><Routed /></Shell>
    </FeedProvider>
  );
}

export default function App() {
  return <AuthProvider><Gate /></AuthProvider>;
}
