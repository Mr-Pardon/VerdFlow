import type { InjectionKey } from "vue";
import { inject } from "vue";

import { useProjects } from "./useProjects";

export type ProjectsState = ReturnType<typeof useProjects>;

export const projectsStateKey: InjectionKey<ProjectsState> =
  Symbol("verdflow-projects-state");

export const requireProjectsState = (): ProjectsState => {
  const state = inject(projectsStateKey);
  if (!state) {
    throw new Error("Projects state is not available");
  }
  return state;
};
