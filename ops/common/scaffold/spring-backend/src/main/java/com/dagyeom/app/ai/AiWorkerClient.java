package com.dagyeom.app.ai;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.util.Map;

/**
 * Python AI Worker 호출 클라이언트.
 * Spring Backend 가 비전/시계열 추론을 ai-worker 에 위임할 때 사용.
 */
@Component
public class AiWorkerClient {

    private final RestClient client;

    public AiWorkerClient(@Value("${ai-worker.url}") String baseUrl) {
        this.client = RestClient.builder().baseUrl(baseUrl).build();
    }

    public InferResult inferGrade(InferRequest req) {
        return post("/infer/grade", req);
    }

    public InferResult inferAnomaly(InferRequest req) {
        return post("/infer/anomaly", req);
    }

    public InferResult inferPredict(InferRequest req) {
        return post("/infer/predict", req);
    }

    private InferResult post(String path, InferRequest req) {
        return client.post()
                .uri(path)
                .body(req)
                .retrieve()
                .body(InferResult.class);
    }

    public record InferRequest(String imagePath, Map<String, Object> data) {}

    public record InferResult(String label, Double score, Map<String, Object> extras) {}
}
