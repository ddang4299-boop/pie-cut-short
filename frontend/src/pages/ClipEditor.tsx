import { Clapperboard } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import { EmptyState, PageHeader } from "../components/states";

/** Clip Editor — checkpoint 2 (Phase 3+). */
export default function ClipEditor() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  return (
    <div className="mx-auto max-w-5xl p-6">
      <PageHeader title={t("pages.editor.title")} subtitle={t("pages.editor.subtitle")} />
      <EmptyState
        icon={Clapperboard}
        message={t("pages.editor.empty")}
        action={{ label: t("pages.editor.action"), onClick: () => navigate("/moments") }}
      />
    </div>
  );
}
