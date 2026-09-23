FROM golang:1.23-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
# Tests are part of the build: a red suite fails the image.
RUN go vet ./... && go test -count=1 ./pii/ && \
    CGO_ENABLED=0 go build -ldflags="-s -w" -o /piigo ./cmd/server

FROM alpine:3.20
RUN adduser -D -u 10001 piigo
USER piigo
COPY --from=build /piigo /piigo
EXPOSE 8090
ENTRYPOINT ["/piigo"]
