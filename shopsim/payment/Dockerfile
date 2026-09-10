FROM golang:1.27.1-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY shopsim ./shopsim
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/service ./shopsim/payment

FROM alpine:3.23
RUN addgroup -S shopsim && adduser -S -G shopsim shopsim
COPY --from=build /out/service /usr/local/bin/service
USER shopsim
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/service"]
