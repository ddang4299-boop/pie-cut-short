import { Download } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import { EmptyState, PageHeader } from "../components/states";

/** Export queue + finished clips (Phase 6). */
export default function Export() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  return (
    <div className="mx-auto max-w-5xl p-6">
      <PageHeader title={t("pages.export.title")} subtitle={t("pages.export.subtitle")} />
      <EmptyState
        icon={Download}
        message={t("pages.export.empty")}
        action={{ label: t("pages.export.action"), onClick: () => navigate("/moments") }}
      />
    </div>
  );
}
