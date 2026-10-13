import { Injectable, Logger, HttpException, HttpStatus } from '@nestjs/common';
import axios, { AxiosInstance } from 'axios';

@Injectable()
export class DifyAdapter {
  private readonly logger = new Logger(DifyAdapter.name);
  private readonly client: AxiosInstance;

  constructor() {
    this.client = axios.create({
      baseURL: process.env.DIFY_API_URL || 'http://dify-api:5001/v1',
      headers: {
        'Authorization': `Bearer ${process.env.DIFY_API_KEY}`,
        'Content-Type': 'application/json'
      },
      timeout: 10000 // 10s timeout
    });
  }

  async invokeWorkflowStream(workflowId: string, inputs: any, user: string): Promise<any> {
    const payload = {
      inputs: inputs,
      response_mode: 'streaming',
      user: user, // Dify internal user mapping
    };

    try {
      this.logger.log(`Invoking Dify Workflow [${workflowId}] for user [${user}]`);
      const response = await this.client.post(`/workflows/run`, payload, {
        responseType: 'stream'
      });
      return response.data;
    } catch (error: any) {
      if (error.response?.status === 429) {
        this.logger.warn(`Dify/Provider Rate Limited! 429 Received. Proceeding to Failover.`);
        throw new HttpException('RATE_LIMITED', HttpStatus.TOO_MANY_REQUESTS);
      }
      this.logger.error(`Dify Invocation Failed: ${error.message}`);
      throw new HttpException('DIFY_UNAVAILABLE', HttpStatus.SERVICE_UNAVAILABLE);
    }
  }
}
