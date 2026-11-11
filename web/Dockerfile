# Next.js in standalone mode (only what is needed to run: ~150 MB instead of the whole node_modules).
#
# Dependencies and build run on the building machine's platform ($BUILDPLATFORM, x86 in CI),
# without emulation: Node under QEMU (arm64) crashes in npm ci ("Illegal instruction"). The output
# is JavaScript, the same on any CPU; only the final stage is for the target platform (arm64).
# The only native binary in the output (sharp) is unused: images.unoptimized in next.config.
FROM --platform=$BUILDPLATFORM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM --platform=$BUILDPLATFORM node:20-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup -S -g 1001 nodejs && adduser -S -u 1001 -G nodejs nextjs
COPY --from=build --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /app/.next/static ./.next/static
USER nextjs

EXPOSE 3000
# API_URL, GOOGLE_CLIENT_ID and DEV_LOGIN_ENABLED are read at runtime (on the server).
CMD ["node", "server.js"]
