export function getFriendlyChatError(message: string) {
  const normalizedMessage = message.toLowerCase();

  if (
    normalizedMessage.includes("quota_exceeded") ||
    normalizedMessage.includes("status code 429") ||
    normalizedMessage.includes("exceeded your current quota") ||
    normalizedMessage.includes("ava.al/billing")
  ) {
    return "درخواست به Dify و مدل AvalAI رسیده، اما کلید API فعلی AvalAI سهمیه یا اعتبار کافی ندارد. اعتبار پنل AvalAI را بررسی یا یک API Key معتبر با شارژ کافی ثبت کنید.";
  }

  if (
    normalizedMessage.includes("api.avalai.ir") &&
    (normalizedMessage.includes("timed out") || normalizedMessage.includes("timeout"))
  ) {
    return "درخواست به Dify رسیده، اما Dify هنگام اتصال به AvalAI با timeout روبه‌رو شده است. اتصال اینترنت، دسترسی به api.avalai.ir و وضعیت مدل را بررسی کنید و دوباره تلاش کنید.";
  }

  if (
    normalizedMessage.includes("openrouter.ai") ||
    normalizedMessage.includes("network is unreachable") ||
    normalizedMessage.includes("timed out") ||
    normalizedMessage.includes("timeout")
  ) {
    return "درخواست به Dify رسیده، اما سرویس مدل هنگام پاسخ‌دهی timeout داده است. اتصال مدل را بررسی کنید و دوباره تلاش کنید.";
  }

  return message;
}
