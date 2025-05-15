import os
import openai
import whisper
import torch
from dotenv import load_dotenv
from typing import Dict
import re
import json
import tempfile

# .env 파일에서 OPENAI_API_KEY 불러오기
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

# 말버릇 분석용 프롬프트
FULL_TEXT_PROMPT = """다음은 발표 스크립트입니다. '음', '어', '그니까', '아마', '그래서', '뭐냐면', '저기', '그게', '그러니까', '아', '네', '예' 등의 말버릇이 포함된 문장만 골라 JSON으로 정리해주세요. 각 문장에 어떤 말버릇이 몇 번 등장했는지도 함께 표시해주세요. 

반드시 다음 형식으로 답변하세요:
[
  {{"문장": "음 저희는 이번에...", "말버릇": {{"음": 1}}}},
  {{"문장": "그니까 아마...", "말버릇": {{"그니까": 1, "아마": 1}}}}
]

텍스트:
{text}"""

class FillerDetector:
    def __init__(self, model_size: str = "medium"):
        self.model_size = model_size
        self.model = None
        
    def _load_model(self):
        """Whisper 모델을 지연 로딩"""
        if self.model is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
            self.model = whisper.load_model(self.model_size, device=device)
    
    def analyze_filler_from_bytes(self, file_content: bytes, verbose: bool = False) -> Dict:
        """
        음성 파일 bytes에서 말버릇 탐지
        
        Args:
            file_content (bytes): 음성 파일 bytes
            verbose (bool): 상세 출력 여부
            
        Returns:
            Dict: 분석 결과
                {
                    "success": bool,
                    "full_text": str,
                    "filler_sentences": list,
                    "total_filler_counts": dict,
                    "total_fillers": int,
                    "total_sentences_with_fillers": int,
                    "error": str (에러 발생시)
                }
        """
        # 임시 파일 생성
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_file:
            temp_file.write(file_content)
            temp_file_path = temp_file.name
        
        try:
            if verbose:
                print("⏳ Whisper로 변환 중...")
            
            # Whisper 모델 로드
            self._load_model()
            
            # 음성을 텍스트로 변환
            result = self.model.transcribe(
                temp_file_path,
                language="ko",
                task="transcribe",
                verbose=False,
                temperature=0.0,
                beam_size=8,
                best_of=8,
                patience=1.5,
                length_penalty=0.8,
                no_speech_threshold=0.1,
                logprob_threshold=-3.0,
                condition_on_previous_text=False,
                suppress_tokens=[],
                word_timestamps=False,
                initial_prompt=(
                    "한국어로 말하는 음성입니다. "
                    "음, 어, 아, 으음, 어어, 그니까, 아마, 그래서, 뭐냐면, 저기, 그게, 그러니까, 네, 예, 응, 혹시, 일단 "
                    "등의 모든 말버릇과 감탄사를 절대 생략하지 말고 말한 그대로 정확히 전사하세요. "
                    "아주 짧은 말버릇이나 머뭇거림도 모두 포함해주세요. "
                    "심지어 '음...', '어...', '아...' 같은 짧은 감탄사도 빠짐없이 전사하세요."
                )
            )
            
            full_text = result["text"]
            if verbose:
                print(f"✅ Whisper 변환 완료!")
                print(f"🤖 LLM으로 말버릇 문장 추출 중...")
            
            # OpenAI LLM으로 말버릇 포함 문장 추출
            return self._extract_filler_sentences(full_text, verbose)
            
        except Exception as e:
            return {
                "success": False,
                "error": f"음성 변환 오류: {str(e)}",
                "full_text": "",
                "filler_sentences": [],
                "total_filler_counts": {},
                "total_fillers": 0,
                "total_sentences_with_fillers": 0
            }
        finally:
            # 임시 파일 삭제
            os.unlink(temp_file_path)
    
    def _extract_filler_sentences(self, full_text: str, verbose: bool = False) -> Dict:
        """OpenAI LLM으로 말버릇 포함 문장 추출"""
        prompt = FULL_TEXT_PROMPT.format(text=full_text)
        
        try:
            response = openai.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=2000
            )
            content = response.choices[0].message.content.strip()
            
            # JSON 추출 및 파싱
            match = re.search(r'\[.*\]', content, re.DOTALL)
            if match:
                json_str = match.group(0)
                filler_sentences = json.loads(json_str)
                
                # 전체 통계 계산
                total_filler_counts = {}
                total_fillers = 0
                
                for sentence_data in filler_sentences:
                    if "말버릇" in sentence_data:
                        for filler, count in sentence_data["말버릇"].items():
                            total_filler_counts[filler] = total_filler_counts.get(filler, 0) + count
                            total_fillers += count
                
                if verbose:
                    print("✅ 말버릇 분석 완료!")
                
                return {
                    "success": True,
                    "full_text": full_text,
                    "filler_sentences": filler_sentences,
                    "total_filler_counts": total_filler_counts,
                    "total_fillers": total_fillers,
                    "total_sentences_with_fillers": len(filler_sentences)
                }
            else:
                return {
                    "success": False,
                    "error": "LLM 응답에서 JSON 형식을 찾을 수 없음",
                    "full_text": full_text,
                    "filler_sentences": [],
                    "total_filler_counts": {},
                    "total_fillers": 0,
                    "total_sentences_with_fillers": 0
                }
        
        except Exception as e:
            return {
                "success": False,
                "error": f"LLM 분석 오류: {str(e)}",
                "full_text": full_text,
                "filler_sentences": [],
                "total_filler_counts": {},
                "total_fillers": 0,
                "total_sentences_with_fillers": 0
            }

# API에서 사용할 간단한 함수
def analyze_filler_from_bytes(file_content: bytes, model_size: str = "medium", verbose: bool = False) -> Dict:
    """
    간편한 사용을 위한 래퍼 함수
    
    Args:
        file_content (bytes): 음성 파일 bytes
        model_size (str): Whisper 모델 크기
        verbose (bool): 상세 출력 여부
        
    Returns:
        Dict: 분석 결과
    """
    detector = FillerDetector(model_size)
    return detector.analyze_filler_from_bytes(file_content, verbose)

# 출력용 함수 (선택적으로 사용)
def print_filler_results(result: Dict):
    """분석 결과를 콘솔에 예쁘게 출력"""
    if not result["success"]:
        print(f"❌ 분석 실패: {result.get('error', '알 수 없는 오류')}")
        return
    
    print("\n" + "="*70)
    print("🎯 말버릇 문장 추출 결과")
    print("="*70)
    
    print(f"🔢 총 말버릇 수: {result['total_fillers']}개")
    print(f"📝 말버릇 포함 문장 수: {result['total_sentences_with_fillers']}개")
    
    if result['total_filler_counts']:
        print(f"\n🗣️ 말버릇별 통계:")
        for filler, count in sorted(result['total_filler_counts'].items(), 
                                   key=lambda x: x[1], reverse=True):
            percentage = round(count / result['total_fillers'] * 100, 1)
            print(f"  - {filler}: {count}회 ({percentage}%)")
        
        print(f"\n📝 말버릇 포함 문장:")
        print("-"*70)
        
        for i, sentence_data in enumerate(result['filler_sentences'], 1):
            print(f"\n[{i}] {sentence_data['문장']}")
            print(f"말버릇: {sentence_data['말버릇']}")
    else:
        print("\n말버릇이 발견되지 않았습니다.")
    
    print("\n" + "="*70)
    print("✅ 분석 완료!")
    print("="*70)

# 테스트용 (기존 기능 유지)
if __name__ == "__main__":
    # 파일 경로로 테스트하는 함수
    def test_with_file(audio_file_path: str):
        from pathlib import Path
        
        if not Path(audio_file_path).exists():
            print(f"❌ 파일을 찾을 수 없습니다: {audio_file_path}")
            return
        
        # 파일을 bytes로 읽어서 테스트
        with open(audio_file_path, 'rb') as f:
            file_content = f.read()
        
        print("🔍 말버릇 문장 추출 테스트 시작...")
        result = analyze_filler_from_bytes(file_content, verbose=True)
        print_filler_results(result)
    
    # 테스트 실행
    audio_file = "test_audio/b1055308.wav"
    test_with_file(audio_file)

def build_filler_map_from_result(filler_result: dict, whisper_segments: list) -> dict[int, str]:
    """
    whisper의 segment 목록과 말버릇 분석 결과(filler_result)를 연결하여,
    segment.id별로 해당 문장에 포함된 말버릇(필러) 문자열을 반환하는 맵을 생성합니다.
    Args:
        filler_result (dict): analyze_filler_from_bytes()의 리턴값 ("filler_sentences" 포함)
        whisper_segments (list): Whisper의 segment 리스트 (각 segment는 'id', 'text' 등 포함)
    Returns:
        dict[int, str]: segment.id별로 필러(말버릇) 문자열 (없으면 '없음')
    """
    # Whisper segment의 텍스트와 filler_result의 문장 매칭 (공백, 구두점 등 유사하게 비교)
    def normalize(text):
        return re.sub(r'\s+', '', re.sub(r'[.,!?…~\-]', '', text)).strip()

    filler_map = {}
    filler_sentences = filler_result.get("filler_sentences", [])
    used = set()

    for seg in whisper_segments:
        seg_id = seg.get("id")
        seg_text = normalize(seg.get("text", ""))
        found = False
        for idx, fs in enumerate(filler_sentences):
            fs_text = normalize(fs.get("문장", ""))
            if fs_text and fs_text in seg_text and idx not in used:
                # 말버릇 dict를 콤마로 연결
                fillers = fs.get("말버릇", {})
                filler_str = ", ".join(fillers.keys()) if fillers else "없음"
                filler_map[seg_id] = filler_str
                used.add(idx)
                found = True
                break
        if not found:
            filler_map[seg_id] = "없음"
    return filler_map