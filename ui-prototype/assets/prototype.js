(function () {
  "use strict";

  const page = document.body.dataset.page;

  if (page !== "login") {
    return;
  }

  const form = document.getElementById("loginForm");
  const account = document.getElementById("account");
  const password = document.getElementById("password");
  const remember = document.getElementById("remember");
  const loginButton = document.getElementById("loginButton");
  const passwordToggle = document.getElementById("passwordToggle");
  const forgotButton = document.getElementById("forgotPassword");
  const forgotDialog = document.getElementById("forgotDialog");
  const closeForgotDialog = document.getElementById("closeForgotDialog");
  const toast = document.getElementById("toast");
  const rememberedAccountKey = "project-risk-remembered-account";
  let toastTimer;

  function showToast(message) {
    window.clearTimeout(toastTimer);
    toast.textContent = message;
    toast.classList.add("is-visible");

    toastTimer = window.setTimeout(function () {
      toast.classList.remove("is-visible");
    }, 2400);
  }

  function setFieldState(input, errorElement, isValid) {
    input.setAttribute("aria-invalid", String(!isValid));
    errorElement.hidden = isValid;
  }

  function validateForm() {
    const accountError = document.getElementById("accountError");
    const passwordError = document.getElementById("passwordError");
    const accountValid = account.value.trim().length > 0;
    const passwordValid = password.value.length > 0;

    setFieldState(account, accountError, accountValid);
    setFieldState(password, passwordError, passwordValid);

    if (!accountValid) {
      account.focus();
    } else if (!passwordValid) {
      password.focus();
    }

    return accountValid && passwordValid;
  }

  function restoreRememberedAccount() {
    try {
      const rememberedAccount = window.localStorage.getItem(rememberedAccountKey);

      if (rememberedAccount) {
        account.value = rememberedAccount;
        remember.checked = true;
      }
    } catch (error) {
      // 浏览器禁用本地存储时，不影响登录页其他交互。
    }
  }

  function storeRememberedAccount() {
    try {
      if (remember.checked) {
        window.localStorage.setItem(rememberedAccountKey, account.value.trim());
      } else {
        window.localStorage.removeItem(rememberedAccountKey);
      }
    } catch (error) {
      // 记住账号属于辅助体验，存储失败不阻止登录。
    }
  }

  function getLoginDestination() {
    const normalizedAccount = account.value.trim().toLowerCase();
    const isSystemAdmin = normalizedAccount === "admin"
      || normalizedAccount === "system_admin"
      || normalizedAccount === "system-admin";

    return isSystemAdmin
      ? {
          message: "登录成功，正在进入管理后台",
          url: "03-admin-dashboard.html"
        }
      : {
          message: "登录成功，正在进入风险看板",
          url: "02-dashboard.html"
        };
  }

  passwordToggle.addEventListener("click", function () {
    const shouldShow = password.type === "password";

    password.type = shouldShow ? "text" : "password";
    passwordToggle.setAttribute("aria-pressed", String(shouldShow));
    passwordToggle.setAttribute("aria-label", shouldShow ? "隐藏密码" : "显示密码");
    password.focus();
  });

  account.addEventListener("input", function () {
    if (account.value.trim()) {
      setFieldState(account, document.getElementById("accountError"), true);
    }
  });

  password.addEventListener("input", function () {
    if (password.value) {
      setFieldState(password, document.getElementById("passwordError"), true);
    }
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();

    if (!validateForm()) {
      return;
    }

    storeRememberedAccount();
    loginButton.disabled = true;
    loginButton.classList.add("is-loading");
    loginButton.setAttribute("aria-label", "正在登录");

    window.setTimeout(function () {
      const destination = getLoginDestination();

      loginButton.disabled = false;
      loginButton.classList.remove("is-loading");
      loginButton.removeAttribute("aria-label");
      showToast(destination.message);
      window.setTimeout(function () {
        window.location.href = destination.url;
      }, 420);
    }, 650);
  });

  forgotButton.addEventListener("click", function () {
    if (typeof forgotDialog.showModal === "function") {
      forgotDialog.showModal();
    } else {
      showToast("请联系系统管理员重置密码");
    }
  });

  closeForgotDialog.addEventListener("click", function () {
    forgotDialog.close();
  });

  forgotDialog.addEventListener("click", function (event) {
    if (event.target === forgotDialog) {
      forgotDialog.close();
    }
  });

  restoreRememberedAccount();
}());
