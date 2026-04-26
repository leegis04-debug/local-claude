# Stage 1: Build
FROM gradle:8.8-jdk21 AS builder
WORKDIR /builder
COPY . .
RUN gradle bootJar --no-daemon

# Stage 2: Extract Spring Boot layers
FROM bellsoft/liberica-openjre-debian:21-cds AS extractor
WORKDIR /extractor
COPY --from=builder /builder/build/libs/*.jar app.jar
RUN java -Djarmode=tools -jar app.jar extract --layers --destination extracted

# Stage 3: Runtime
FROM bellsoft/liberica-openjre-debian:21-cds
WORKDIR /application
COPY --from=extractor /extractor/extracted/dependencies/ ./
COPY --from=extractor /extractor/extracted/spring-boot-loader/ ./
COPY --from=extractor /extractor/extracted/snapshot-dependencies/ ./
COPY --from=extractor /extractor/extracted/application/ ./

RUN useradd -m -u 1000 appuser && chown -R appuser /application
USER appuser

EXPOSE 8080
ENTRYPOINT ["java", "-jar", "application.jar"]
