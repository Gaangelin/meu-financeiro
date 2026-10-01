FROM node:20-alpine

WORKDIR /app

COPY . .

CMD ["node", "MEFI_Backend_Novo.js"]
