export interface RoleDiagramMark {
  role_key: string;
  kind: "area" | "tree" | "shrub" | "groundcover";
  x: number;
  y: number;
  size: number;
  points: [number, number][];
}

export interface RoleGuide {
  group_id: string;
  group_order: number;
  group_name: string;
  group_description: string;
  suggested_assets: string[];
  note: string;
  diagram: RoleDiagramMark[];
}

export interface PresentedRole {
  role_key: string;
  display_name: string;
  description: string;
  required?: boolean;
  guide?: RoleGuide | null;
}

const COLORS = [
  "#55856d",
  "#5a7c9e",
  "#ac8250",
  "#956c8c",
  "#718c47",
  "#477f88",
];
export const roleColor = (roles: PresentedRole[], key: string) =>
  COLORS[
    Math.max(
      0,
      roles.findIndex((role) => role.role_key === key),
    ) % COLORS.length
  ];

export const roleGroups = (roles: PresentedRole[]) => {
  const groups = new Map<
    string,
    { id: string; name: string; description: string; roles: PresentedRole[] }
  >();
  for (const role of roles) {
    const id = role.guide?.group_id ?? "";
    let group = groups.get(id);
    if (!group) {
      group = {
        id,
        name: role.guide?.group_name ?? "",
        description: role.guide?.group_description ?? "",
        roles: [],
      };
      groups.set(id, group);
    }
    group.roles.push(role);
  }
  return [...groups.values()];
};
export function withRoleGroupEnabled(
  roles: PresentedRole[],
  enabled: Record<string, boolean>,
  roleKeys: string[],
  checked: boolean,
): Record<string, boolean> {
  const keys = new Set(roleKeys);
  const next = {
    ...enabled,
    ...Object.fromEntries(
      roles
        .filter((role) => keys.has(role.role_key))
        .map((role) => [role.role_key, checked]),
    ),
  };
  return roles.some((role) => next[role.role_key] !== false) ? next : enabled;
}
