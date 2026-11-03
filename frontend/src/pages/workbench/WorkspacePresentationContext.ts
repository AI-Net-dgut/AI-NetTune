import { createContext } from 'react';

export type WorkspaceTab = 'data' | 'diagnosis' | 'plans';
export interface WorkspacePresentation {
  diagnosisHost: HTMLElement | null;
  plansHost: HTMLElement | null;
  canViewPlans: boolean;
  showPlans: () => void;
}
export const WorkspacePresentationContext = createContext<WorkspacePresentation | null>(null);

