import { Component, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router";

import { ErrorState } from "./states";

type Props = { children: ReactNode; message: string };
type State = { error: Error | null };

class Boundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error("Page crashed", error);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="p-6" data-testid="page-crash">
        <ErrorState
          message={this.props.message}
          onRetry={() => this.setState({ error: null })}
          log={`${error.name}: ${error.message}\n${error.stack ?? ""}`}
        />
      </div>
    );
  }
}

/** Keeps the sidebar and header usable when one page throws while rendering; resets on navigation. */
export function PageErrorBoundary({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  return (
    <Boundary key={pathname} message={t("common.page_crashed")}>
      {children}
    </Boundary>
  );
}
