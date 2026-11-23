import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router";

import { Header } from "./components/Header";
import { JobsDrawer } from "./components/JobsDrawer";
import { NAV_ITEMS } from "./components/nav";
import { PageErrorBoundary } from "./components/PageErrorBoundary";
import { Sidebar } from "./components/Sidebar";
import ClipEditor from "./pages/ClipEditor";
import Export from "./pages/Export";
import Library from "./pages/Library";
import MomentsReview from "./pages/MomentsReview";
import Settings from "./pages/Settings";

export default function App() {
  const [jobsOpen, setJobsOpen] = useState(false);
  const navigate = useNavigate();

  // Global shortcuts: Alt+1..5 switch pages, Esc closes the Jobs drawer.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setJobsOpen(false);
        return;
      }
      if (!event.altKey || event.ctrlKey || event.metaKey || event.shiftKey)
        return;
      const digit = event.code.startsWith("Digit")
        ? event.code.slice(5)
        : event.key;
      const item = NAV_ITEMS.find((n) => n.shortcut === digit);
      if (item) {
        event.preventDefault();
        navigate(item.path);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [navigate]);

  return (
    <div className="flex h-full bg-app">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Header onOpenJobs={() => setJobsOpen(true)} />
        <main className="app-glow flex-1 overflow-y-auto">
          <PageErrorBoundary>
            <Routes>
              <Route path="/" element={<Navigate to="/library" replace />} />
              <Route path="/library" element={<Library />} />
              <Route path="/moments" element={<MomentsReview />} />
              <Route path="/editor" element={<ClipEditor />} />
              <Route path="/export" element={<Export />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="*" element={<Navigate to="/library" replace />} />
            </Routes>
          </PageErrorBoundary>
        </main>
      </div>
      <JobsDrawer open={jobsOpen} onClose={() => setJobsOpen(false)} />
    </div>
  );
}
