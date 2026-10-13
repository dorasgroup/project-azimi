import { NestFactory } from '@nestjs/core';
import { AppModule } from './app.module';
import { GlobalExceptionFilter } from './chat/presentation/filters/global-exception.filter';

async function bootstrap() {
  const app = await NestFactory.create(AppModule);
  app.enableCors(); // In production, limit CORS
  
  // Register Exception Filter Globally
  app.useGlobalFilters(new GlobalExceptionFilter());

  // Nginx API Gateway will route requests here.
  // Port 3000 inside the container.
  await app.listen(3000);
  console.log(`AI Gateway is running on: ${await app.getUrl()}`);
}
bootstrap();
