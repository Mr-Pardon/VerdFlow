import { createRouter, createWebHashHistory } from "vue-router";

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", redirect: "/projects" },
    {
      path: "/projects",
      component: () => import("../features/projects/ProjectsPage.vue"),
    },
    {
      path: "/dts",
      component: () => import("../features/dts/DtsHome.vue"),
    },
    {
      path: "/blender",
      component: () => import("../features/blender/BlenderHome.vue"),
    },
    {
      path: "/dts/realtime",
      component: () => import("../features/dts-live/DtsLivePage.vue"),
    },
    {
      path: "/projects/:projectId/dts",
      component: () => import("../features/dts/DtsProjectView.vue"),
    },
    {
      path: "/strategies",
      component: () => import("../features/library/StrategyLibraryPage.vue"),
    },
    {
      path: "/assets",
      component: () => import("../features/library/AssetLibraryPage.vue"),
    },
    { path: "/plans", name: "planting-plans", component: () => import("../features/library/PlantingPlansPage.vue") },
    { path: "/plans/:profileId", name: "planting-plan", component: () => import("../features/library/BindingProfilesPage.vue") },
    { path: "/bindings", redirect: "/plans" },
    { path: "/bindings/:target(dts|blender)", redirect: "/plans" },
    { path: "/bindings/:target(dts|blender)/:strategyId/:strategyVersion", redirect: to =>
      typeof to.query.profile === "string" ? `/plans/${encodeURIComponent(to.query.profile)}` : "/plans" },
    {
      path: "/model-services",
      component: () =>
        import("../features/model-services/ModelServicesPage.vue"),
    },
    {
      path: "/settings",
      component: () => import("../features/settings/SettingsPage.vue"),
    },
  ],
});
