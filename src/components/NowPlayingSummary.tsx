import { Field, PanelSection, PanelSectionRow } from "@decky/ui";
import { NowPlayingPerformance } from "./NowPlayingPerformance";

interface Props {
  title: string;
  details: string[];
  performanceAppId?: string;
}

export function NowPlayingSummary({ title, details, performanceAppId }: Props) {
  return (
    <PanelSection>
      <PanelSectionRow>
        <div>
          <Field label={title} description={details.filter(Boolean).join(" | ")} />
          {performanceAppId && <NowPlayingPerformance appId={performanceAppId} />}
        </div>
      </PanelSectionRow>
    </PanelSection>
  );
}
