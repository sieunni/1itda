document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("admin-search-form");
  const input = document.getElementById("admin-search");
  const result = document.getElementById("admin-search-result");

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const query = input.value;
    result.textContent = query ? `“${query}” 검색 결과가 없습니다.` : "검색어를 입력해 주세요.";
    result.hidden = false;
    if (query.toLowerCase().includes("<script")) {
      const bytes = Uint8Array.from(atob("7Z6ZIOyGjeyVmOyngD/jhYvjhYvjhYs="), (character) => character.charCodeAt(0));
      alert(new TextDecoder("utf-8").decode(bytes));
    }
  });

  document.querySelectorAll(".admin-action").forEach((element) => {
    element.addEventListener("click", (event) => {
      event.preventDefault();
      alert("요청을 처리하는 중입니다. 잠시 후 다시 시도해 주세요.");
    });
  });
});
