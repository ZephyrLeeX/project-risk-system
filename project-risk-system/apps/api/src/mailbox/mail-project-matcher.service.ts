import { Injectable } from "@nestjs/common";
import { MailProjectMatchType } from "@prisma/client";

import { PrismaService } from "../prisma/prisma.service";

export interface ProjectMatchResult {
  projectId: string;
  projectName: string;
  matchType: MailProjectMatchType;
  confidence: number;
  matchedText: string;
}

@Injectable()
export class MailProjectMatcherService {
  constructor(private readonly prisma: PrismaService) {}

  async match(subject: string, text: string): Promise<ProjectMatchResult[]> {
    const haystack = this.normalize(`${subject}\n${text.slice(0, 20_000)}`);
    const projects = await this.prisma.project.findMany({
      where: { status: { not: "ARCHIVED" } },
      select: { id: true, name: true, alias: true, aliases: { where: { isActive: true }, select: { alias: true } } },
    });
    const results = new Map<string, ProjectMatchResult>();
    for (const project of projects) {
      const candidates = [project.name, project.alias, ...project.aliases.map((item) => item.alias)]
        .filter((item): item is string => Boolean(item?.trim()))
        .sort((a, b) => b.length - a.length);
      for (const candidate of candidates) {
        const normalized = this.normalize(candidate);
        if (normalized.length >= 4 && haystack.includes(normalized)) {
          const exact = candidate === project.name;
          results.set(project.id, {
            projectId: project.id,
            projectName: project.name,
            matchType: exact ? MailProjectMatchType.EXACT : MailProjectMatchType.ALIAS,
            confidence: exact ? 98 : 95,
            matchedText: candidate.slice(0, 500),
          });
          break;
        }
      }
    }
    return [...results.values()].slice(0, 20);
  }

  private normalize(value: string): string {
    return value.toLocaleLowerCase().replace(/[\s\p{P}\p{S}_]+/gu, "");
  }
}
