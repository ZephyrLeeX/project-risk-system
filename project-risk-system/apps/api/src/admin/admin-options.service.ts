import { Injectable } from "@nestjs/common";
import { ProjectStatus } from "@prisma/client";

import type {
  DepartmentOption,
  ProjectOption,
} from "@risk-platform/contracts";

import { PrismaService } from "../prisma/prisma.service";

@Injectable()
export class AdminOptionsService {
  constructor(private readonly prisma: PrismaService) {}

  departments(): Promise<DepartmentOption[]> {
    return this.prisma.department.findMany({
      where: { enabled: true },
      select: {
        id: true,
        code: true,
        name: true,
      },
      orderBy: [{ sortOrder: "asc" }, { name: "asc" }],
    });
  }

  async projects(): Promise<ProjectOption[]> {
    const projects = await this.prisma.project.findMany({
      where: { status: { not: ProjectStatus.ARCHIVED } },
      select: {
        id: true,
        externalCode: true,
        name: true,
        department: {
          select: {
            name: true,
          },
        },
      },
      orderBy: [{ name: "asc" }],
      take: 500,
    });
    return projects.map((project) => ({
      id: project.id,
      externalCode: project.externalCode,
      name: project.name,
      departmentName: project.department?.name ?? null,
    }));
  }
}
