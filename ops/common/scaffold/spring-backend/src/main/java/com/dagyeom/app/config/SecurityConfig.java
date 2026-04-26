package com.dagyeom.app.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.web.SecurityFilterChain;

/**
 * 기본 SecurityConfig — 개발 시작용 최소 설정.
 * 운영/멀티테넌트 시 RBAC + RLS + JWT 등을 추가.
 */
@Configuration
public class SecurityConfig {

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        http
            .csrf(csrf -> csrf.disable())
            .authorizeHttpRequests(auth -> auth
                .requestMatchers("/actuator/**", "/api/chat/**").permitAll()
                .anyRequest().permitAll()  // TODO: 실제 권한 정책으로 교체
            );
        return http.build();
    }
}
