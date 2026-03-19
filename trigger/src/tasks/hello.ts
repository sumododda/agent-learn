import { task } from "@trigger.dev/sdk";

export const helloTask = task({
  id: "hello-smoke-test",
  run: async (payload: { message: string }) => {
    console.log(`Hello from Trigger.dev! Message: ${payload.message}`);
    return { success: true, echo: payload.message };
  },
});
