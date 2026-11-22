import { Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import { EmptyState, PageHeader } from "../components/states";

/** Moments Review — checkpoint 1 (Phase 2). */
export default function MomentsReview() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  return (
    <div className="mx-auto max-w-5xl p-6">
      <PageHeader title={t("pages.moments.title")} subtitle={t("pages.moments.subtitle")} />
      <EmptyState
        icon={Sparkles}
        message={t("pages.moments.empty")}
        action={{ label: t("pages.moments.action"), onClick: () => navigate("/library") }}
      />
    </div>
  );
}
