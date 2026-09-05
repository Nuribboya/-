"""명령줄에서 빠르게 채점 결과를 확인하기 위한 CLI."""
import argparse

from .grader import grade


def main():
    parser = argparse.ArgumentParser(description="수학 문제 채점 및 해설 생성")
    parser.add_argument("problem", help="문제 (예: '2x + 3 = 11')")
    parser.add_argument("student_answer", help="학생 답안 (예: 'x = 4')")
    args = parser.parse_args()

    result = grade(args.problem, args.student_answer)

    print(f"문제: {result.problem}")
    print(f"유형: {result.problem_type}")
    print("풀이 과정:")
    for i, step in enumerate(result.steps, 1):
        print(f"  {i}. {step}")
    print(f"정답: {result.correct_answer}")
    print(f"학생 답안: {result.student_answer}")
    if result.is_correct is True:
        print("채점 결과: 정답")
    elif result.is_correct is False:
        print("채점 결과: 오답")
    else:
        print(f"채점 결과: 판정 불가 ({result.note})")


if __name__ == "__main__":
    main()
