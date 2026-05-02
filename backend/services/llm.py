from __future__ import annotations

from backend.db.database import get_settings
from backend.db.vector import RetrievalChunk
from backend.config import settings
import re


class LLMService:
    def answer(self, query: str, contexts: list[RetrievalChunk], difficulty: str) -> str:
        prompt = get_settings("system_prompt") or "친절한 클라우드 학습 도우미입니다."
        
        # 인사말 처리
        greetings = ["안녕", "하이", "반갑", "hello", "hi"]
        if any(g in query.lower() for g in greetings):
            return "안녕하세요! ☁️ 클라우드 학습 도우미입니다.\n\n클라우드 용어나 개념에 대해 궁금한 점이 있다면 언제든 편하게 물어보세요!"
            
        if not contexts:
            return f"죄송합니다. '{query}'와(과) 관련된 정보를 문서에서 찾을 수 없었습니다. 다른 키워드로 다시 질문해 주시겠어요?"
            
        # --- 진짜 AI (Gemini) 연동 ---
        if settings.LLM_API_KEY:
            try:
                import google.generativeai as genai
                genai.configure(api_key=settings.LLM_API_KEY)
                model = genai.GenerativeModel('gemini-2.5-flash')
                
                # 여러 문서의 내용을 하나로 합침
                context_text = "\n\n".join([f"[문서: {c.title}]\n{c.content}" for c in contexts])
                
                ai_prompt = f"""[시스템 지침]
{prompt}
- 반드시 제공된 [참고 문서] 내용만을 바탕으로 답변하세요.
- 답변 내에서 특정 페이지를 언급할 때는 반드시 해당 내용이 포함된 [문서: ...] 제목의 페이지 번호(p.XX)를 정확하게 인용하세요.
- 만약 여러 문서가 제공되었다면, 각 문서의 차이점이나 보완적인 내용을 종합하여 답변하세요.

[참고 문서]
{context_text}

[질문]
{query}

위의 시스템 지침을 반드시 준수하여 답변하세요. 답변은 '{difficulty}' 수준에 맞춰야 합니다.
"""
                response = model.generate_content(ai_prompt)
                
                # 실제 검색된 모든 페이지 번호와 파일명을 매핑 (중복 제거 및 그룹화)
                from collections import defaultdict
                ref_dict = defaultdict(list)
                for c in contexts:
                    name = re.sub(r'\s*\(p\.\d+\)\s*', '', c.title).strip()
                    ref_dict[name].append(f"{c.page}p")
                
                ref_parts = []
                for name in sorted(ref_dict.keys()):
                    pages = ", ".join(sorted(list(set(ref_dict[name]))))
                    ref_parts.append(f"- {name}: {pages}")
                
                additional_info = f"\n\n📚 **참고 문헌:**\n" + "\n".join(ref_parts)
                return response.text + additional_info
            except Exception as e:
                return f"⚠️ AI 생성 중 오류가 발생했습니다. (API 키를 확인해주세요): {str(e)}"
        # -----------------------------

        # 여러 컨텍스트를 조합하여 답변 생성 (Mock)
        main_context = contexts[0]
        # (기존 import re 제거됨)
        # "(p.XX)" 페이지 텍스트 제거
        clean_title = re.sub(r'\s*\(p\.\d+\)\s*', '', main_context.title).strip()
        # 내용에서 과도한 줄바꿈과 저작권 문구 제거
        clean_content = re.sub(r'\n+', ' ', main_context.content)
        clean_content = re.sub(r'(?:©|\(c\)|\(C\)).*?Reserved\.', '', clean_content, flags=re.IGNORECASE)
        clean_content = re.sub(r'[\->|]', '', clean_content) # 의미없는 기호 제거
        clean_content = re.sub(r'\s+', ' ', clean_content).strip()
        
        # 질문 키워드 주변의 문맥(Snippet) 추출 로직
        q_clean = re.sub(r'[^\w\s]', '', query).lower()
        tokens = q_clean.split()
        keyword = tokens[0] if tokens else query.lower()
        idx = clean_content.lower().find(keyword)
        
        if idx != -1:
            start = max(0, idx - 40)
            end = min(len(clean_content), idx + 120)
            snippet = clean_content[start:end]
            if start > 0: snippet = "..." + snippet
            if end < len(clean_content): snippet = snippet + "..."
        else:
            snippet = clean_content[:150] + "..."
        
        # 실제 검색된 모든 페이지 번호와 파일명을 매핑 (Mock 모드용 그룹화)
        from collections import defaultdict
        ref_dict = defaultdict(list)
        for c in contexts:
            name = re.sub(r'\s*\(p\.\d+\)\s*', '', c.title).strip()
            ref_dict[name].append(f"{c.page}p")
        
        ref_parts = []
        for name in sorted(ref_dict.keys()):
            pages = ", ".join(sorted(list(set(ref_dict[name]))))
            ref_parts.append(f"- {name}: {pages}")
        
        additional_info = f"\n\n📚 **참고 문헌:**\n" + "\n".join(ref_parts) if contexts else ""

        response = ""
        # Mock 모드에서는 프롬프트를 직접 노출하지 않고 답변 스타일로만 사용합니다.
        if difficulty == "고급":
            response = f"🎓 **맞춤형 고급 답변**\n\n전문적인 관점에서 설명해 드립니다. {clean_title} 문서에 따르면,\n\n{snippet}\n\n시스템 설계 시 이 부분을 깊게 고려해 보세요.{additional_info}"
        elif difficulty == "중급":
            response = f"📝 **핵심 요약**\n\n{clean_title}에서 설명하는 주요 포인트는 다음과 같습니다.\n\n{snippet}\n\n개념 구조를 파악하는 데 도움이 되실 거예요.{additional_info}"
        else:
            response = f"💡 **신입사원을 위한 쉬운 설명**\n\n{clean_title} 문서에서 찾은 관련 내용이에요.\n\n'{snippet}'\n\n이해하기 어렵다면 다시 물어봐 주세요!{additional_info}"
            
        return response
