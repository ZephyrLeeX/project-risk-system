import { ValidationPipe } from "@nestjs/common";
import { ConfigService } from "@nestjs/config";
import { NestFactory } from "@nestjs/core";
import cookieParser = require("cookie-parser");
import "reflect-metadata";

import { AppModule } from "./app.module";

async function bootstrap(): Promise<void> {
  const app = await NestFactory.create(AppModule, {
    bufferLogs: true,
  });
  const config = app.get(ConfigService);
  const port = config.get<number>("API_PORT", 3000);
  const corsOrigin = config.get<string>("CORS_ORIGIN", "http://localhost:5173");

  app.setGlobalPrefix("api");
  app.use(cookieParser());
  app.enableCors({
    origin: corsOrigin.split(",").map((item) => item.trim()),
    credentials: true,
  });
  app.useGlobalPipes(
    new ValidationPipe({
      whitelist: true,
      forbidNonWhitelisted: true,
      transform: true,
    }),
  );
  app.enableShutdownHooks();

  await app.listen(port, "0.0.0.0");
}

void bootstrap();
