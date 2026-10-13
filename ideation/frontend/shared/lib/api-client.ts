import axios, { AxiosError } from 'axios';

export const apiClient = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_BASE_URL || '/api/v1',
  timeout: 10000,
  withCredentials: true,
});

apiClient.interceptors.response.use(
  (response) => response.data,
  (error: AxiosError<any>) => {
    const defaultMessage = 'خطایی در ارتباط با سرور رخ داد.';
    const mappedError = {
      message: error.response?.data?.message || defaultMessage,
      status: error.response?.status,
      errors: error.response?.data?.errors || [],
    };
    return Promise.reject(mappedError);
  }
);
