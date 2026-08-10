import { Injectable } from "@nestjs/common";
import {
  DataScopeType,
  Prisma,
  ProjectStatus,
} from "@prisma/client";

export function buildProjectScopeWhere(
  userId: string,
  dataScope: DataScopeType,
): Prisma.ProjectWhereInput {
  const activeProject = {
    status: { not: ProjectStatus.ARCHIVED },
  } satisfies Prisma.ProjectWhereInput;

  switch (dataScope) {
    case DataScopeType.ALL:
      return activeProject;
    case DataScopeType.OWNED:
      return { ...activeProject, managerId: userId };
    case DataScopeType.ASSIGNED:
      return {
        ...activeProject,
        userScopes: { some: { userId } },
      };
    case DataScopeType.OWNED_OR_ASSIGNED:
      return {
        ...activeProject,
        OR: [
          { managerId: userId },
          { userScopes: { some: { userId } } },
        ],
      };
    case DataScopeType.NONE:
    default:
      return { id: { equals: "00000000-0000-0000-0000-000000000000" } };
  }
}

@Injectable()
export class DataScopeService {
  forUser(userId: string, dataScope: DataScopeType): Prisma.ProjectWhereInput {
    return buildProjectScopeWhere(userId, dataScope);
  }
}
